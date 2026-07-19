from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import Q

from api.models import CommsChannel, PersistentAgent, PersistentAgentMessage
from api.models import PersistentAgentStep, PersistentAgentSystemStep

from .message_reads import is_peer_dm_message


@dataclass(frozen=True)
class InboundRoutingScope:
    agent_id: UUID
    message_id: UUID | None


_inbound_routing_scope: ContextVar[InboundRoutingScope | None] = ContextVar("inbound_routing_scope", default=None)


def capture_inbound_routing_scope(
    agent: PersistentAgent, *, pending_inbound: bool = False, background_before: datetime | None = None
) -> InboundRoutingScope:
    message = _latest_inbound_message(agent, exclude_webhooks=pending_inbound)
    is_background = bool(
        message
        and not pending_inbound
        and (
            _is_inbound_webhook(message)
            or _has_newer_background_trigger(agent, message, before=background_before)
        )
    )
    return InboundRoutingScope(agent_id=agent.id, message_id=None if is_background or message is None else message.id)


def bind_inbound_routing_scope(scope: InboundRoutingScope) -> Token:
    return _inbound_routing_scope.set(scope)


def reset_inbound_routing_scope(token: Token) -> None:
    _inbound_routing_scope.reset(token)


def get_bound_inbound_routing_scope(agent: PersistentAgent) -> InboundRoutingScope | None:
    scope = _inbound_routing_scope.get()
    return scope if scope is not None and scope.agent_id == agent.id else None


def get_current_inbound_message(agent: PersistentAgent) -> PersistentAgentMessage | None:
    scope = get_bound_inbound_routing_scope(agent)
    if scope is not None:
        if scope.message_id is None:
            return None
        return PersistentAgentMessage.objects.filter(id=scope.message_id, owner_agent=agent).select_related(
            "conversation", "from_endpoint"
        ).first()

    message = _latest_inbound_message(agent)
    if message is None or _is_inbound_webhook(message) or _has_newer_background_trigger(agent, message):
        return None
    return message


def _latest_inbound_message(
    agent: PersistentAgent, *, exclude_webhooks: bool = False
) -> PersistentAgentMessage | None:
    messages = PersistentAgentMessage.objects.filter(
        owner_agent=agent, is_outbound=False, conversation__isnull=False
    )
    if exclude_webhooks:
        messages = messages.exclude(
            conversation__channel=CommsChannel.OTHER, raw_payload__source_kind="webhook"
        )
    return messages.select_related("conversation", "from_endpoint").order_by("-timestamp", "-seq").first()


def _is_inbound_webhook(message: PersistentAgentMessage) -> bool:
    payload = message.raw_payload
    return message.conversation.channel == CommsChannel.OTHER and isinstance(payload, dict) and (
        str(payload.get("source_kind", "")).strip().lower() == "webhook"
    )


def _has_newer_background_trigger(
    agent: PersistentAgent, message: PersistentAgentMessage, *, before: datetime | None = None
) -> bool:
    steps = PersistentAgentStep.objects.filter(agent=agent, created_at__gt=message.timestamp)
    if before is not None:
        steps = steps.filter(created_at__lte=before)
    background = Q(cron_trigger__isnull=False) | Q(
        system_step__code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER
    )
    return steps.filter(background).exists()


def get_latest_inbound_human_message(agent: PersistentAgent) -> PersistentAgentMessage | None:
    message = get_current_inbound_message(agent)
    return None if is_peer_dm_message(message) else message


def get_message_sender_address(message: PersistentAgentMessage) -> str:
    return message.from_endpoint.address if message.from_endpoint_id else message.conversation.address
