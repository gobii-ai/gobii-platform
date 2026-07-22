"""Shared management service for persistent-agent inbound and outbound webhooks."""

from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from api.models import PersistentAgent, PersistentAgentInboundWebhook, PersistentAgentWebhook
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


class AgentWebhookError(ValueError):
    """Safe validation or lookup error for webhook management callers."""


def build_inbound_webhook_url(
    webhook: PersistentAgentInboundWebhook,
    *,
    base_url: str | None = None,
) -> str:
    resolved_base = settings.PUBLIC_SITE_URL if base_url is None else base_url
    resolved_base = str(resolved_base or "").strip().rstrip("/")
    path = reverse("api:inbound_agent_webhook", kwargs={"webhook_id": webhook.id})
    endpoint = f"{resolved_base}{path}" if resolved_base else path
    return f"{endpoint}?{urlencode({'t': webhook.secret})}"


def _serialize_inbound(
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
    }
    if include_url:
        result["url"] = build_inbound_webhook_url(webhook, base_url=base_url)
    return result


def _serialize_outbound(webhook: PersistentAgentWebhook, *, include_url: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(webhook.id),
        "name": webhook.name,
        "last_triggered_at": webhook.last_triggered_at.isoformat() if webhook.last_triggered_at else None,
        "last_response_status": webhook.last_response_status,
    }
    if include_url:
        result.update(url=webhook.url, last_error_message=webhook.last_error_message)
    return result


class AgentWebhookService:
    def __init__(
        self,
        agent: PersistentAgent,
        *,
        actor_user_id: object | None = None,
        source: AnalyticsSource = AnalyticsSource.AGENT,
        inbound_base_url: str | None = None,
    ) -> None:
        self.agent = agent
        self.actor_user_id = actor_user_id
        self.source = source
        self.inbound_base_url = inbound_base_url

    def _get(self, manager, webhook_id: object, direction: str):
        try:
            return manager.get(id=webhook_id)
        except (manager.model.DoesNotExist, ValidationError, ValueError) as exc:
            raise AgentWebhookError(f"{direction.title()} webhook not found for this agent.") from exc

    def _track(self, webhook, event: AnalyticsEvent) -> None:
        if not self.actor_user_id:
            return
        properties = Analytics.with_org_properties(
            {
                "agent_id": str(self.agent.id),
                "agent_name": self.agent.name,
                "webhook_id": str(webhook.id),
                "webhook_name": webhook.name,
            },
            organization=self.agent.organization,
        )
        if isinstance(webhook, PersistentAgentInboundWebhook):
            properties["is_active"] = webhook.is_active
        transaction.on_commit(
            lambda: Analytics.track_event(
                user_id=self.actor_user_id,
                event=event,
                source=self.source,
                properties=properties.copy(),
            )
        )

    def _save(self, webhook, direction: str, event: AnalyticsEvent) -> None:
        try:
            webhook.full_clean()
            with transaction.atomic():
                webhook.save()
        except ValidationError as exc:
            message = "; ".join(exc.messages) or str(exc)
            raise AgentWebhookError(f"Unable to save {direction} webhook: {message}") from exc
        except IntegrityError as exc:
            raise AgentWebhookError(
                f"An {direction} webhook with that name already exists for this agent."
            ) from exc
        self._track(webhook, event)

    def manage_inbound(
        self,
        action: str,
        *,
        webhook_id: object = None,
        name: object = None,
        is_active: bool | None = None,
    ) -> dict[str, object]:
        action = str(action or "").strip().lower()
        if action not in {"list", "get", "create", "update", "rotate_secret", "delete"}:
            raise AgentWebhookError("Unsupported action. Use list, get, create, update, rotate_secret, or delete.")
        if action == "list":
            return {"webhooks": [_serialize_inbound(hook) for hook in self.agent.inbound_webhooks.order_by("name")]}
        if action == "create":
            normalized_name = str(name or "").strip()
            if not normalized_name:
                raise AgentWebhookError("Inbound webhook name is required.")
            webhook = PersistentAgentInboundWebhook(
                agent=self.agent,
                name=normalized_name,
                is_active=True if is_active is None else bool(is_active),
            )
            self._save(webhook, "inbound", AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_ADDED)
            return {"webhook": _serialize_inbound(webhook, include_url=True, base_url=self.inbound_base_url)}

        webhook = self._get(self.agent.inbound_webhooks, webhook_id, "inbound")
        if action == "get":
            return {"webhook": _serialize_inbound(webhook, include_url=True, base_url=self.inbound_base_url)}
        if action == "update":
            if name is None and is_active is None:
                raise AgentWebhookError("Provide name or is_active to update the inbound webhook.")
            if name is not None:
                webhook.name = str(name).strip()
                if not webhook.name:
                    raise AgentWebhookError("Inbound webhook name cannot be empty.")
            if is_active is not None:
                webhook.is_active = bool(is_active)
            self._save(webhook, "inbound", AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_UPDATED)
            return {"webhook": _serialize_inbound(webhook)}
        if action == "rotate_secret":
            webhook.rotate_secret()
            self._track(webhook, AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_SECRET_ROTATED)
            return {
                "message": "Inbound webhook secret rotated; the previous endpoint URL no longer works.",
                "webhook": _serialize_inbound(webhook, include_url=True, base_url=self.inbound_base_url),
            }
        if action == "delete":
            result = _serialize_inbound(webhook)
            self._track(webhook, AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_DELETED)
            webhook.delete()
            return {"deleted_webhook": result}
        raise AgentWebhookError("Unsupported inbound webhook action.")

    def manage_outbound(
        self,
        action: str,
        *,
        webhook_id: object = None,
        name: object = None,
        url: object = None,
    ) -> dict[str, object]:
        action = str(action or "").strip().lower()
        if action not in {"list", "get", "create", "update", "delete"}:
            raise AgentWebhookError("Unsupported action. Use list, get, create, update, or delete.")
        if action == "list":
            return {"webhooks": [_serialize_outbound(hook) for hook in self.agent.webhooks.order_by("name")]}
        if action == "create":
            normalized_name = str(name or "").strip()
            normalized_url = str(url or "").strip()
            if not normalized_name or not normalized_url:
                raise AgentWebhookError("Outbound webhook name and URL are required.")
            webhook = PersistentAgentWebhook(agent=self.agent, name=normalized_name, url=normalized_url)
            self._save(webhook, "outbound", AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_ADDED)
            return {"webhook": _serialize_outbound(webhook, include_url=True)}

        webhook = self._get(self.agent.webhooks, webhook_id, "outbound")
        if action == "get":
            return {"webhook": _serialize_outbound(webhook, include_url=True)}
        if action == "update":
            if name is None and url is None:
                raise AgentWebhookError("Provide name or url to update the outbound webhook.")
            if name is not None:
                webhook.name = str(name).strip()
                if not webhook.name:
                    raise AgentWebhookError("Outbound webhook name cannot be empty.")
            if url is not None:
                webhook.url = str(url).strip()
                if not webhook.url:
                    raise AgentWebhookError("Outbound webhook URL cannot be empty.")
            self._save(webhook, "outbound", AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_UPDATED)
            return {"webhook": _serialize_outbound(webhook, include_url=True)}
        if action == "delete":
            result = _serialize_outbound(webhook)
            self._track(webhook, AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_DELETED)
            webhook.delete()
            return {"deleted_webhook": result}
        raise AgentWebhookError("Unsupported outbound webhook action.")
