from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.core.mail import EmailMultiAlternatives


APP_SUPPORT_MESSAGE_MAX_LENGTH = 4000
APP_SUPPORT_SUBJECT = "Gobii app support request"
AGENT_MESSAGE_REPORT_SUBJECT = "Gobii message report"


class SupportRequestConfigurationError(RuntimeError):
    pass


def clean_support_message(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Message is required.")

    message = value.strip()
    if not message:
        raise ValueError("Message is required.")
    if len(message) > APP_SUPPORT_MESSAGE_MAX_LENGTH:
        raise ValueError(f"Message must be {APP_SUPPORT_MESSAGE_MAX_LENGTH} characters or fewer.")
    return message


def _clean_optional_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _format_workspace_context(value: Any) -> str:
    if not isinstance(value, dict):
        return ""

    context_type = _clean_optional_text(value.get("type"))
    context_id = _clean_optional_text(value.get("id"))
    context_name = _clean_optional_text(value.get("name"))
    parts = []
    if context_type:
        parts.append(f"type={context_type}")
    if context_id:
        parts.append(f"id={context_id}")
    if context_name:
        parts.append(f"name={context_name}")
    return ", ".join(parts)


def build_app_support_email_body(*, user: AbstractBaseUser, payload: dict[str, Any], message: str) -> str:
    page_url = _clean_optional_text(payload.get("pageUrl"))
    agent_id = _clean_optional_text(payload.get("agentId"))
    agent_name = _clean_optional_text(payload.get("agentName"))
    workspace_context = _format_workspace_context(payload.get("workspaceContext"))

    lines = [
        "A user submitted an in-app support request.",
        "",
        "User",
        f"ID: {user.pk}",
        f"Email: {getattr(user, 'email', '') or '-'}",
    ]

    if page_url:
        lines.extend(["", "Page", page_url])

    if agent_id or agent_name:
        lines.extend([
            "",
            "Agent",
            f"ID: {agent_id or '-'}",
            f"Name: {agent_name or '-'}",
        ])

    if workspace_context:
        lines.extend(["", "Workspace", workspace_context])

    lines.extend(["", "Message", message])
    return "\n".join(lines)


def send_app_support_request(*, user: AbstractBaseUser, payload: dict[str, Any]) -> None:
    message = clean_support_message(payload.get("message"))
    recipient = settings.SUPPORT_EMAIL
    if not recipient:
        raise SupportRequestConfigurationError("Support email is not configured.")

    body = build_app_support_email_body(user=user, payload=payload, message=message)
    user_email = getattr(user, "email", "") or ""
    reply_to = [user_email] if user_email else []

    email = EmailMultiAlternatives(
        APP_SUPPORT_SUBJECT,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        reply_to=reply_to,
    )
    email.send(fail_silently=False)


def build_agent_message_report_email_body(*, user: AbstractBaseUser, agent: Any, message: Any, comment: str) -> str:
    message_channel = "-"
    if message.conversation_id:
        message_channel = message.conversation.channel
    elif message.from_endpoint_id:
        message_channel = message.from_endpoint.channel

    lines = [
        "A user reported an agent message.",
        "",
        "Reporter",
        f"ID: {user.pk}",
        f"Email: {getattr(user, 'email', '') or '-'}",
        "",
        "Agent",
        f"ID: {agent.id}",
        f"Name: {agent.name or '-'}",
        "",
        "Message",
        f"ID: {message.id}",
        f"Timestamp: {message.timestamp.isoformat() if message.timestamp else '-'}",
        f"Channel: {message_channel}",
        "",
        message.body or "-",
    ]

    if comment:
        lines.extend(["", "Reporter comment", comment])

    return "\n".join(lines)


def send_agent_message_report_email(*, user: AbstractBaseUser, agent: Any, message: Any, comment: str) -> None:
    recipient = settings.SUPPORT_EMAIL
    if not recipient:
        raise SupportRequestConfigurationError("Support email is not configured.")

    reporter_email = getattr(user, "email", "") or ""
    reply_to = [reporter_email] if reporter_email else []
    email = EmailMultiAlternatives(
        AGENT_MESSAGE_REPORT_SUBJECT,
        build_agent_message_report_email_body(user=user, agent=agent, message=message, comment=comment),
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        reply_to=reply_to,
    )
    email.send(fail_silently=False)
