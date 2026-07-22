"""Shared management helpers for persistent-agent inbound and outbound webhooks."""

from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from api.models import PersistentAgent, PersistentAgentInboundWebhook, PersistentAgentWebhook
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


class AgentWebhookError(ValueError):
    """Safe validation or lookup error for webhook management callers."""


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return "; ".join(
            str(message)
            for messages in exc.message_dict.values()
            for message in messages
        )
    if hasattr(exc, "messages"):
        return "; ".join(str(message) for message in exc.messages)
    return str(exc)


def _track_event(
    agent: PersistentAgent,
    webhook,
    event: AnalyticsEvent,
    *,
    actor_user_id: object | None,
    source: AnalyticsSource,
) -> None:
    if not actor_user_id:
        return
    properties = Analytics.with_org_properties(
        {
            "agent_id": str(agent.id),
            "agent_name": agent.name,
            "webhook_id": str(webhook.id),
            "webhook_name": webhook.name,
        },
        organization=agent.organization,
    )
    if isinstance(webhook, PersistentAgentInboundWebhook):
        properties["is_active"] = webhook.is_active
    transaction.on_commit(
        lambda: Analytics.track_event(
            user_id=actor_user_id,
            event=event,
            source=source,
            properties=properties.copy(),
        )
    )


def build_inbound_webhook_url(
    webhook: PersistentAgentInboundWebhook,
    *,
    base_url: str | None = None,
) -> str:
    """Return the secret-bearing public endpoint for an inbound webhook."""
    resolved_base = settings.PUBLIC_SITE_URL if base_url is None else base_url
    resolved_base = str(resolved_base or "").strip().rstrip("/")
    path = reverse("api:inbound_agent_webhook", kwargs={"webhook_id": webhook.id})
    endpoint = f"{resolved_base}{path}" if resolved_base else path
    return f"{endpoint}?{urlencode({'t': webhook.secret})}"


def serialize_inbound_webhook(
    webhook: PersistentAgentInboundWebhook,
    *,
    include_url: bool = False,
    base_url: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(webhook.id),
        "name": webhook.name,
        "is_active": webhook.is_active,
        "last_triggered_at": webhook.last_triggered_at.isoformat() if webhook.last_triggered_at else None,
        "created_at": webhook.created_at.isoformat() if webhook.created_at else None,
        "updated_at": webhook.updated_at.isoformat() if webhook.updated_at else None,
    }
    if include_url:
        result["url"] = build_inbound_webhook_url(webhook, base_url=base_url)
    return result


def serialize_outbound_webhook(
    webhook: PersistentAgentWebhook,
    *,
    include_url: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(webhook.id),
        "name": webhook.name,
        "last_triggered_at": webhook.last_triggered_at.isoformat() if webhook.last_triggered_at else None,
        "last_response_status": webhook.last_response_status,
        "created_at": webhook.created_at.isoformat() if webhook.created_at else None,
        "updated_at": webhook.updated_at.isoformat() if webhook.updated_at else None,
    }
    if include_url:
        result["url"] = webhook.url
        result["last_error_message"] = webhook.last_error_message
    return result


def get_inbound_webhook(agent: PersistentAgent, webhook_id: object) -> PersistentAgentInboundWebhook:
    try:
        return agent.inbound_webhooks.get(id=webhook_id)
    except (PersistentAgentInboundWebhook.DoesNotExist, ValidationError, ValueError) as exc:
        raise AgentWebhookError("Inbound webhook not found for this agent.") from exc


def get_outbound_webhook(agent: PersistentAgent, webhook_id: object) -> PersistentAgentWebhook:
    try:
        return agent.webhooks.get(id=webhook_id)
    except (PersistentAgentWebhook.DoesNotExist, ValidationError, ValueError) as exc:
        raise AgentWebhookError("Outbound webhook not found for this agent.") from exc


def create_inbound_webhook(
    agent: PersistentAgent,
    *,
    name: str,
    is_active: bool = True,
    actor_user_id: object | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> PersistentAgentInboundWebhook:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise AgentWebhookError("Inbound webhook name is required.")
    webhook = PersistentAgentInboundWebhook(
        agent=agent,
        name=normalized_name,
        is_active=bool(is_active),
    )
    try:
        webhook.full_clean()
        with transaction.atomic():
            webhook.save()
    except ValidationError as exc:
        raise AgentWebhookError(f"Unable to save inbound webhook: {_validation_message(exc)}") from exc
    except IntegrityError as exc:
        raise AgentWebhookError("An inbound webhook with that name already exists for this agent.") from exc
    _track_event(
        agent,
        webhook,
        AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_ADDED,
        actor_user_id=actor_user_id,
        source=source,
    )
    return webhook


def update_inbound_webhook(
    agent: PersistentAgent,
    webhook_id: object,
    *,
    name: str | None = None,
    is_active: bool | None = None,
    actor_user_id: object | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> PersistentAgentInboundWebhook:
    webhook = get_inbound_webhook(agent, webhook_id)
    if name is None and is_active is None:
        raise AgentWebhookError("Provide name or is_active to update the inbound webhook.")
    if name is not None:
        normalized_name = str(name).strip()
        if not normalized_name:
            raise AgentWebhookError("Inbound webhook name cannot be empty.")
        webhook.name = normalized_name
    if is_active is not None:
        webhook.is_active = bool(is_active)
    try:
        webhook.full_clean()
        with transaction.atomic():
            webhook.save()
    except ValidationError as exc:
        raise AgentWebhookError(f"Unable to save inbound webhook: {_validation_message(exc)}") from exc
    except IntegrityError as exc:
        raise AgentWebhookError("An inbound webhook with that name already exists for this agent.") from exc
    _track_event(
        agent,
        webhook,
        AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_UPDATED,
        actor_user_id=actor_user_id,
        source=source,
    )
    return webhook


def rotate_inbound_webhook_secret(
    agent: PersistentAgent,
    webhook_id: object,
    *,
    actor_user_id: object | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> PersistentAgentInboundWebhook:
    webhook = get_inbound_webhook(agent, webhook_id)
    webhook.rotate_secret()
    _track_event(
        agent,
        webhook,
        AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_SECRET_ROTATED,
        actor_user_id=actor_user_id,
        source=source,
    )
    return webhook


def delete_inbound_webhook(
    agent: PersistentAgent,
    webhook_id: object,
    *,
    actor_user_id: object | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> dict[str, object]:
    webhook = get_inbound_webhook(agent, webhook_id)
    result = serialize_inbound_webhook(webhook)
    _track_event(
        agent,
        webhook,
        AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_DELETED,
        actor_user_id=actor_user_id,
        source=source,
    )
    webhook.delete()
    return result


def create_outbound_webhook(
    agent: PersistentAgent,
    *,
    name: str,
    url: str,
    actor_user_id: object | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> PersistentAgentWebhook:
    normalized_name = str(name or "").strip()
    normalized_url = str(url or "").strip()
    if not normalized_name or not normalized_url:
        raise AgentWebhookError("Outbound webhook name and URL are required.")
    webhook = PersistentAgentWebhook(agent=agent, name=normalized_name, url=normalized_url)
    try:
        webhook.full_clean()
        with transaction.atomic():
            webhook.save()
    except ValidationError as exc:
        raise AgentWebhookError(f"Unable to save outbound webhook: {_validation_message(exc)}") from exc
    except IntegrityError as exc:
        raise AgentWebhookError("An outbound webhook with that name already exists for this agent.") from exc
    _track_event(
        agent,
        webhook,
        AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_ADDED,
        actor_user_id=actor_user_id,
        source=source,
    )
    return webhook


def update_outbound_webhook(
    agent: PersistentAgent,
    webhook_id: object,
    *,
    name: str | None = None,
    url: str | None = None,
    actor_user_id: object | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> PersistentAgentWebhook:
    webhook = get_outbound_webhook(agent, webhook_id)
    if name is None and url is None:
        raise AgentWebhookError("Provide name or url to update the outbound webhook.")
    if name is not None:
        normalized_name = str(name).strip()
        if not normalized_name:
            raise AgentWebhookError("Outbound webhook name cannot be empty.")
        webhook.name = normalized_name
    if url is not None:
        normalized_url = str(url).strip()
        if not normalized_url:
            raise AgentWebhookError("Outbound webhook URL cannot be empty.")
        webhook.url = normalized_url
    try:
        webhook.full_clean()
        with transaction.atomic():
            webhook.save()
    except ValidationError as exc:
        raise AgentWebhookError(f"Unable to save outbound webhook: {_validation_message(exc)}") from exc
    except IntegrityError as exc:
        raise AgentWebhookError("An outbound webhook with that name already exists for this agent.") from exc
    _track_event(
        agent,
        webhook,
        AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_UPDATED,
        actor_user_id=actor_user_id,
        source=source,
    )
    return webhook


def delete_outbound_webhook(
    agent: PersistentAgent,
    webhook_id: object,
    *,
    actor_user_id: object | None = None,
    source: AnalyticsSource = AnalyticsSource.AGENT,
) -> dict[str, object]:
    webhook = get_outbound_webhook(agent, webhook_id)
    result = serialize_outbound_webhook(webhook)
    _track_event(
        agent,
        webhook,
        AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_DELETED,
        actor_user_id=actor_user_id,
        source=source,
    )
    webhook.delete()
    return result
