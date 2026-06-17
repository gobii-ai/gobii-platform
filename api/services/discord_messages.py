"""Shared Discord conversation and debounce helpers."""

import logging
from datetime import timedelta
from typing import Mapping

import requests
from django.conf import settings
from django.utils import timezone

from api.models import (
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
)
from api.services.inbound_debounce import process_inbound_debounce, schedule_inbound_processing

logger = logging.getLogger(__name__)

DISCORD_INBOUND_DEBOUNCE_DEADLINE_KEY = "agent:discord-inbound-debounce:{agent_id}:deadline"
DISCORD_INBOUND_DEBOUNCE_SCHEDULED_KEY = "agent:discord-inbound-debounce:{agent_id}:scheduled"
DISCORD_INBOUND_TYPING_CHANNEL_KEY = "agent:discord-inbound-debounce:{agent_id}:typing-channel"
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_TYPING_INDICATOR_TIMEOUT_SECONDS = 2


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


def send_discord_typing_indicator(channel_id: str) -> bool:
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id or not settings.DISCORD_BOT_TOKEN:
        return False
    try:
        response = requests.post(
            f"{DISCORD_API_BASE}/channels/{normalized_channel_id}/typing",
            headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"},
            timeout=DISCORD_TYPING_INDICATOR_TIMEOUT_SECONDS,
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
    normalized_agent_id = str(agent_id)
    deadline_key, scheduled_key = discord_inbound_debounce_keys(normalized_agent_id)
    typing_channel_key = discord_inbound_typing_channel_key(normalized_agent_id)

    def extra_pipeline_writes(pipeline, ttl: int) -> None:
        if normalized_typing_channel_id:
            pipeline.set(typing_channel_key, normalized_typing_channel_id, ex=ttl)

    def task_factory():
        from api.agent.tasks.process_events import process_discord_inbound_debounce_task

        return process_discord_inbound_debounce_task

    return schedule_inbound_processing(
        normalized_agent_id,
        debounce_seconds=debounce_seconds,
        deadline_key=deadline_key,
        scheduled_key=scheduled_key,
        process_callback=process_agent_events_after_discord_debounce,
        task_factory=task_factory,
        log_label="Discord",
        extra_pipeline_writes=extra_pipeline_writes,
        delete_keys=(typing_channel_key,),
    )


def process_discord_inbound_debounce(agent_id: str) -> None:
    debounce_seconds = discord_inbound_debounce_seconds()
    normalized_agent_id = str(agent_id)
    deadline_key, scheduled_key = discord_inbound_debounce_keys(normalized_agent_id)
    typing_channel_key = discord_inbound_typing_channel_key(normalized_agent_id)

    def before_deadline_check(redis_client) -> None:
        typing_channel_id = redis_client.get(typing_channel_key)
        if isinstance(typing_channel_id, (bytes, bytearray)):
            typing_channel_id = typing_channel_id.decode("utf-8", "ignore")
        typing_channel_id = str(typing_channel_id or "").strip()
        if typing_channel_id:
            send_discord_typing_indicator(typing_channel_id)

    def task_factory():
        from api.agent.tasks.process_events import process_discord_inbound_debounce_task

        return process_discord_inbound_debounce_task

    process_inbound_debounce(
        normalized_agent_id,
        debounce_seconds=debounce_seconds,
        deadline_key=deadline_key,
        scheduled_key=scheduled_key,
        process_callback=process_agent_events_after_discord_debounce,
        task_factory=task_factory,
        log_label="Discord",
        before_deadline_check=before_deadline_check,
        extra_expire_keys=(typing_channel_key,),
        delete_keys=(typing_channel_key,),
    )
