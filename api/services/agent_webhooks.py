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


def build_inbound_webhook_url(webhook: PersistentAgentInboundWebhook, *, base_url: str | None = None) -> str:
    base = str(settings.PUBLIC_SITE_URL if base_url is None else base_url or "").strip().rstrip("/")
    path = reverse("api:inbound_agent_webhook", kwargs={"webhook_id": webhook.id})
    return f"{base}{path}?{urlencode({'t': webhook.secret})}"


def _serialize(webhook, *, include_url: bool = False, base_url: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {"id": str(webhook.id), "name": webhook.name}
    result["last_triggered_at"] = webhook.last_triggered_at.isoformat() if webhook.last_triggered_at else None
    if isinstance(webhook, PersistentAgentInboundWebhook):
        result["is_active"] = webhook.is_active
        if include_url:
            result["url"] = build_inbound_webhook_url(webhook, base_url=base_url)
    else:
        result["last_response_status"] = webhook.last_response_status
        if include_url:
            result.update(url=webhook.url, last_error_message=webhook.last_error_message)
    return result


class AgentWebhookService:
    def __init__(self, agent: PersistentAgent, *, actor_user_id: object | None = None,
                 source: AnalyticsSource = AnalyticsSource.AGENT, inbound_base_url: str | None = None) -> None:
        self.agent = agent
        self.actor_user_id = actor_user_id
        self.source = source
        self.inbound_base_url = inbound_base_url

    def _track(self, webhook, event: AnalyticsEvent) -> None:
        if not self.actor_user_id:
            return
        properties = {
            "agent_id": str(self.agent.id),
            "agent_name": self.agent.name,
            "webhook_id": str(webhook.id),
            "webhook_name": webhook.name,
        }
        if isinstance(webhook, PersistentAgentInboundWebhook):
            properties["is_active"] = webhook.is_active
        properties = Analytics.with_org_properties(properties, organization=self.agent.organization)
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=self.actor_user_id, event=event, source=self.source, properties=properties.copy()
        ))

    def _save(self, webhook, direction: str, event: AnalyticsEvent) -> None:
        try:
            webhook.full_clean()
            with transaction.atomic():
                webhook.save()
        except ValidationError as exc:
            message = "; ".join(exc.messages) or str(exc)
            raise AgentWebhookError(f"Unable to save {direction} webhook: {message}") from exc
        except IntegrityError as exc:
            raise AgentWebhookError(f"An {direction} webhook with that name already exists for this agent.") from exc
        self._track(webhook, event)

    def manage(self, direction: str, action: str, *, webhook_id: object = None, name: object = None,
               is_active: bool | None = None, url: object = None) -> dict[str, object]:
        inbound = direction == "inbound"
        label = "Inbound" if inbound else "Outbound"
        allowed = {"list", "get", "create", "update", "delete"}
        if inbound:
            allowed.add("rotate_secret")
        action = str(action or "").strip().lower()
        if action not in allowed:
            choices = ", ".join(sorted(allowed - {"delete"})) + ", or delete"
            raise AgentWebhookError(f"Unsupported action. Use {choices}.")

        manager = self.agent.inbound_webhooks if inbound else self.agent.webhooks
        serialize = lambda hook, include_url=False: _serialize(
            hook, include_url=include_url, base_url=self.inbound_base_url
        )
        if action == "list":
            return {"webhooks": [serialize(hook) for hook in manager.order_by("name")]}
        if action == "create":
            clean_name = str(name or "").strip()
            clean_url = str(url or "").strip()
            if not clean_name or (not inbound and not clean_url):
                required = "name" if inbound else "name and URL"
                raise AgentWebhookError(f"{label} webhook {required} {'is' if inbound else 'are'} required.")
            webhook = PersistentAgentInboundWebhook(
                agent=self.agent, name=clean_name, is_active=True if is_active is None else bool(is_active)
            ) if inbound else PersistentAgentWebhook(agent=self.agent, name=clean_name, url=clean_url)
            event = (AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_ADDED if inbound
                     else AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_ADDED)
            self._save(webhook, direction, event)
            return {"webhook": serialize(webhook, include_url=True)}

        try:
            webhook = manager.get(id=webhook_id)
        except (manager.model.DoesNotExist, ValidationError, ValueError) as exc:
            raise AgentWebhookError(f"{label} webhook not found for this agent.") from exc
        if action == "get":
            return {"webhook": serialize(webhook, include_url=True)}
        if action == "delete":
            result = serialize(webhook)
            event = (AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_DELETED if inbound
                     else AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_DELETED)
            self._track(webhook, event)
            webhook.delete()
            return {"deleted_webhook": result}
        if action == "rotate_secret":
            webhook.rotate_secret()
            self._track(webhook, AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_SECRET_ROTATED)
            return {"message": "Inbound webhook secret rotated; the previous endpoint URL no longer works.",
                    "webhook": serialize(webhook, include_url=True)}

        changes = {"name": name, "is_active": is_active} if inbound else {"name": name, "url": url}
        changes = {field: value for field, value in changes.items() if value is not None}
        if not changes:
            fields = "name or is_active" if inbound else "name or url"
            raise AgentWebhookError(f"Provide {fields} to update the {direction} webhook.")
        for field, value in changes.items():
            value = bool(value) if field == "is_active" else str(value).strip()
            if value == "":
                raise AgentWebhookError(f"{label} webhook {'URL' if field == 'url' else 'name'} cannot be empty.")
            setattr(webhook, field, value)
        event = (AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_UPDATED if inbound
                 else AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_UPDATED)
        self._save(webhook, direction, event)
        return {"webhook": serialize(webhook, include_url=not inbound)}
