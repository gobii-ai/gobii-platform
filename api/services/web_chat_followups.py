from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model

from api.agent.comms.message_reads import latest_visible_outbound_message_queryset
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentMessageRead,
    UserPhoneNumber,
    parse_web_user_address,
)
from api.services.email_verification import has_verified_email


@dataclass(slots=True)
class UnseenWebChatFollowup:
    agent: PersistentAgent
    message: PersistentAgentMessage
    directive: str


def prepare_unseen_web_chat_followup(message_id: str) -> UnseenWebChatFollowup | None:
    message = (
        PersistentAgentMessage.objects.select_related(
            "owner_agent",
            "owner_agent__preferred_contact_endpoint",
            "to_endpoint",
            "conversation",
        )
        .filter(id=message_id)
        .first()
    )
    if not _is_web_chat_outbound_message(message):
        return None

    agent = message.owner_agent
    if agent is None or not agent.is_active or agent.is_deleted:
        return None

    recipient_user = _recipient_user_for_web_message(message)
    if recipient_user is None:
        return None

    if _message_has_been_read(message, recipient_user):
        return None

    if not _is_latest_relevant_outbound_web_message(message):
        return None

    contact = _resolve_unseen_web_chat_followup_contact(agent, recipient_user)
    if contact is None:
        return None

    return UnseenWebChatFollowup(
        agent=agent,
        message=message,
        directive=_unseen_web_chat_followup_directive(message, contact),
    )


def has_unseen_web_chat_followup_contact(message) -> bool:
    if not _is_web_chat_outbound_message(message):
        return False

    agent = message.owner_agent
    if agent is None or not agent.is_active or agent.is_deleted:
        return False

    recipient_user = _recipient_user_for_web_message(message)
    if recipient_user is None:
        return False

    return _resolve_unseen_web_chat_followup_contact(agent, recipient_user) is not None


def _is_web_chat_outbound_message(message) -> bool:
    if message is None or not message.is_outbound:
        return False
    if not message.owner_agent_id:
        return False
    if message.to_endpoint_id and message.to_endpoint.channel == CommsChannel.WEB:
        return True
    if message.conversation_id and message.conversation.channel == CommsChannel.WEB:
        return True
    return False


def _recipient_user_for_web_message(message):
    if not message.to_endpoint_id:
        return None
    user_id, agent_id = parse_web_user_address(message.to_endpoint.address)
    if agent_id != str(message.owner_agent_id) or user_id is None:
        return None
    return get_user_model().objects.filter(id=user_id).first()


def _is_latest_relevant_outbound_web_message(message) -> bool:
    latest = (
        latest_visible_outbound_message_queryset()
        .filter(
            owner_agent_id=message.owner_agent_id,
            to_endpoint_id=message.to_endpoint_id,
            conversation_id=message.conversation_id,
        )
        .first()
    )
    return bool(latest and latest.id == message.id)


def _message_has_been_read(message, user) -> bool:
    return PersistentAgentMessageRead.objects.filter(
        message=message,
        user=user,
    ).exists()


def _agent_has_sending_endpoint(agent, channel: str) -> bool:
    return agent.comms_endpoints.filter(channel=channel).exists()


def _verified_email_address_for_user(user) -> str | None:
    email = (getattr(user, "email", "") or "").strip()
    if email and has_verified_email(user):
        return email
    return None


def _verified_phone_for_user(user, address: str | None = None) -> str | None:
    queryset = UserPhoneNumber.objects.filter(user=user, is_verified=True)
    if address:
        normalized = PersistentAgentCommsEndpoint.normalize_address(CommsChannel.SMS, address)
        queryset = queryset.filter(phone_number__iexact=normalized)
    phone = queryset.order_by("-is_primary", "id").first()
    return phone.phone_number if phone else None


def _preferred_non_web_followup_contact(agent, user) -> dict[str, str] | None:
    endpoint = agent.preferred_contact_endpoint
    if endpoint is None or endpoint.channel not in (CommsChannel.EMAIL, CommsChannel.SMS):
        return None

    if endpoint.channel == CommsChannel.EMAIL:
        email = _verified_email_address_for_user(user)
        if not email or email.lower() != (endpoint.address or "").strip().lower():
            return None
        if not _agent_has_sending_endpoint(agent, CommsChannel.EMAIL):
            return None
        if not agent.is_recipient_whitelisted(CommsChannel.EMAIL, endpoint.address):
            return None
        return {"channel": CommsChannel.EMAIL, "address": endpoint.address, "tool": "send_email"}

    phone = _verified_phone_for_user(user, endpoint.address)
    if not phone:
        return None
    if not _agent_has_sending_endpoint(agent, CommsChannel.SMS):
        return None
    if not agent.is_recipient_whitelisted(CommsChannel.SMS, endpoint.address):
        return None
    return {"channel": CommsChannel.SMS, "address": endpoint.address, "tool": "send_sms"}


def _fallback_non_web_followup_contact(agent, user) -> dict[str, str] | None:
    email = _verified_email_address_for_user(user)
    if (
        email
        and _agent_has_sending_endpoint(agent, CommsChannel.EMAIL)
        and agent.is_recipient_whitelisted(CommsChannel.EMAIL, email)
    ):
        return {"channel": CommsChannel.EMAIL, "address": email, "tool": "send_email"}

    phone = _verified_phone_for_user(user)
    if (
        phone
        and _agent_has_sending_endpoint(agent, CommsChannel.SMS)
        and agent.is_recipient_whitelisted(CommsChannel.SMS, phone)
    ):
        return {"channel": CommsChannel.SMS, "address": phone, "tool": "send_sms"}

    return None


def _resolve_unseen_web_chat_followup_contact(agent, user) -> dict[str, str] | None:
    return _preferred_non_web_followup_contact(agent, user) or _fallback_non_web_followup_contact(agent, user)


def _unseen_web_chat_followup_directive(message, contact: dict[str, str]) -> str:
    return (
        "Unread web chat follow-up "
        f"(message_id={message.id}): The latest web chat message to this user has not been seen "
        f"after {int(settings.WEB_CHAT_UNSEEN_FOLLOWUP_DELAY_SECONDS)} seconds. "
        f"They have another contact channel available: {contact['channel']} at {contact['address']}. "
        f"Consider following up concisely using {contact['tool']} if the user still needs to see or answer the message. "
        "Do not duplicate the web chat message verbatim unless that is the clearest useful follow-up."
    )
