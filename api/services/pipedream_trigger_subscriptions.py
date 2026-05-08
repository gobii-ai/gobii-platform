"""Pipedream Connect trigger subscription provisioning and ingestion."""

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, Mapping

import requests
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.tools.mcp_manager import get_mcp_manager
from api.integrations.pipedream_connect import create_connect_session
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentPipedreamTriggerSubscription,
)

PIPEDREAM_API_BASE = "https://api.pipedream.com/v1"
DISCORD_APP_SLUG = "discord"
DISCORD_MESSAGE_EVENT_TYPE = "message.created"
DISCORD_MESSAGE_TRIGGER_KEY = "discord-new-message"
DISCORD_MESSAGE_TRIGGER_VERSION = "1.0.3"
SIGNATURE_TOLERANCE_SECONDS = 300
DISCORD_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")


class PipedreamTriggerSubscriptionError(RuntimeError):
    """Raised when a subscription action cannot be completed."""


class PipedreamTriggerSignatureError(ValueError):
    """Raised when a Pipedream trigger delivery signature is invalid."""


@dataclass(frozen=True)
class EnsureSubscriptionResult:
    subscription: PersistentAgentPipedreamTriggerSubscription | None
    created: bool = False
    reused: bool = False
    action_required: bool = False
    connect_url: str = ""
    message: str = ""


@dataclass(frozen=True)
class TriggerTargetOption:
    label: str
    value: str


@dataclass(frozen=True)
class DiscoverTargetsResult:
    targets: list[TriggerTargetOption]
    target_type: str = "channel"
    action_required: bool = False
    connect_url: str = ""
    message: str = ""


def _https_base_url() -> str:
    current_site = Site.objects.get_current()
    domain = current_site.domain.strip().rstrip("/")
    return f"https://{domain}"


def _pipedream_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-pd-environment": settings.PIPEDREAM_ENVIRONMENT,
    }


def _get_pipedream_access_token() -> str:
    token = get_mcp_manager().get_pipedream_access_token() or ""
    if not token:
        raise PipedreamTriggerSubscriptionError(
            "Pipedream is not configured. Set PIPEDREAM_CLIENT_ID and PIPEDREAM_CLIENT_SECRET."
        )
    if not settings.PIPEDREAM_PROJECT_ID:
        raise PipedreamTriggerSubscriptionError("PIPEDREAM_PROJECT_ID is not configured.")
    return token


def _subscription_webhook_url(subscription: PersistentAgentPipedreamTriggerSubscription) -> str:
    path = reverse("api:pipedream_trigger_subscription_webhook", args=[subscription.id])
    return f"{_https_base_url()}{path}?t={subscription.webhook_secret}"


def _normalize_channel_id(raw: object) -> str:
    channel_id = str(raw or "").strip()
    if not channel_id:
        raise PipedreamTriggerSubscriptionError("At least one channel ID is required.")
    if not DISCORD_SNOWFLAKE_RE.fullmatch(channel_id):
        raise PipedreamTriggerSubscriptionError(
            "Discord channel IDs must be numeric Discord snowflakes. "
            "Do not pass placeholders like <<<DISCORD_CHANNEL_ID>>>."
        )
    return channel_id


def _channel_name_for(channel_id: str, channel_names: Mapping[str, object] | None) -> str:
    if not isinstance(channel_names, Mapping):
        return ""
    raw_name = channel_names.get(channel_id)
    if not isinstance(raw_name, str):
        return ""
    return raw_name.strip()[:255]


def _discord_configured_props(channel_id: str, account_id: str) -> dict[str, object]:
    return {
        "discord": {
            "authProvisionId": account_id,
        },
        "channels": [channel_id],
    }


def _trigger_key_for(app_slug: str, event_type: str) -> str:
    if app_slug == DISCORD_APP_SLUG and event_type == DISCORD_MESSAGE_EVENT_TYPE:
        return DISCORD_MESSAGE_TRIGGER_KEY
    raise PipedreamTriggerSubscriptionError("Only Discord message subscriptions are supported in v1.")


def _trigger_version_for(app_slug: str, event_type: str) -> str:
    if app_slug == DISCORD_APP_SLUG and event_type == DISCORD_MESSAGE_EVENT_TYPE:
        return DISCORD_MESSAGE_TRIGGER_VERSION
    raise PipedreamTriggerSubscriptionError("Only Discord message subscriptions are supported in v1.")


def _target_prop_name_for(app_slug: str, event_type: str) -> str:
    if app_slug == DISCORD_APP_SLUG and event_type == DISCORD_MESSAGE_EVENT_TYPE:
        return "channels"
    raise PipedreamTriggerSubscriptionError("Only Discord message subscriptions are supported in v1.")


def _raise_for_status(response: requests.Response, *, action: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = (getattr(response, "text", "") or "")[:1000]
        message = f"Pipedream {action} failed with HTTP {response.status_code}."
        if response_text:
            message = f"{message} Response: {response_text}"
        raise PipedreamTriggerSubscriptionError(message) from exc


def _active_account_id(agent: PersistentAgent, app_slug: str, token: str) -> str:
    response = requests.get(
        f"{PIPEDREAM_API_BASE}/connect/{settings.PIPEDREAM_PROJECT_ID}/accounts",
        params={
            "external_user_id": str(agent.id),
            "app": app_slug,
            "limit": 10,
        },
        headers=_pipedream_headers(token),
        timeout=20,
    )
    _raise_for_status(response, action="account lookup")
    for account in (response.json() or {}).get("data") or []:
        if not isinstance(account, dict):
            continue
        if account.get("dead") is True or account.get("healthy") is False:
            continue
        app = account.get("app") or {}
        if isinstance(app, dict) and app.get("name_slug") == app_slug:
            return str(account.get("id") or "").strip()
    return ""


def _retrieve_component_definition(
    *,
    token: str,
    component_id: str,
    version: str,
) -> dict[str, object]:
    params = {"version": version} if version else None
    response = requests.get(
        f"{PIPEDREAM_API_BASE}/connect/{settings.PIPEDREAM_PROJECT_ID}/components/{component_id}",
        params=params,
        headers=_pipedream_headers(token),
        timeout=20,
    )
    _raise_for_status(response, action="component lookup")
    data = (response.json() or {}).get("data") or {}
    if not isinstance(data, dict):
        raise PipedreamTriggerSubscriptionError("Pipedream component lookup returned an invalid response.")
    return data


def _component_props(component: Mapping[str, object]) -> list[dict[str, object]]:
    props = component.get("configurable_props") or []
    if not isinstance(props, list):
        return []
    return [prop for prop in props if isinstance(prop, dict)]


def _app_prop_name(component: Mapping[str, object], app_slug: str) -> str:
    for prop in _component_props(component):
        if prop.get("type") == "app" and prop.get("app") == app_slug:
            name = str(prop.get("name") or "").strip()
            if name:
                return name
    return app_slug


def _configured_props_for_account(
    *,
    component: Mapping[str, object],
    app_slug: str,
    account_id: str,
) -> dict[str, object]:
    return {
        _app_prop_name(component, app_slug): {
            "authProvisionId": account_id,
        }
    }


def _coerce_target_options(payload: Mapping[str, object]) -> tuple[list[TriggerTargetOption], Mapping[str, object]]:
    raw_options = payload.get("options")
    options: list[TriggerTargetOption] = []
    if isinstance(raw_options, list):
        for raw_option in raw_options:
            if not isinstance(raw_option, Mapping):
                continue
            value = str(raw_option.get("value") or "").strip()
            label = str(raw_option.get("label") or value).strip()
            if value:
                options.append(TriggerTargetOption(label=label or value, value=value))

    string_options = payload.get("stringOptions")
    if isinstance(string_options, list):
        for raw_value in string_options:
            value = str(raw_value or "").strip()
            if value:
                options.append(TriggerTargetOption(label=value, value=value))

    context = payload.get("context")
    return options, context if isinstance(context, Mapping) else {}


def _unique_targets(options: Iterable[TriggerTargetOption], *, limit: int) -> list[TriggerTargetOption]:
    unique: list[TriggerTargetOption] = []
    seen: set[str] = set()
    for option in options:
        if not option.value or option.value in seen:
            continue
        seen.add(option.value)
        unique.append(option)
        if len(unique) >= limit:
            break
    return unique


def _configure_component_prop_options(
    *,
    token: str,
    component_id: str,
    version: str,
    external_user_id: str,
    prop_name: str,
    configured_props: Mapping[str, object],
    query: str = "",
    limit: int = 100,
) -> list[TriggerTargetOption]:
    targets: list[TriggerTargetOption] = []
    prev_context: Mapping[str, object] = {}
    for page in range(5):
        payload: dict[str, object] = {
            "id": component_id,
            "external_user_id": external_user_id,
            "prop_name": prop_name,
            "blocking": True,
            "configured_props": dict(configured_props),
            "page": page,
        }
        if version:
            payload["version"] = version
        if query:
            payload["query"] = query
        if prev_context:
            payload["prev_context"] = dict(prev_context)

        response = requests.post(
            f"{PIPEDREAM_API_BASE}/connect/{settings.PIPEDREAM_PROJECT_ID}/components/configure",
            json=payload,
            headers=_pipedream_headers(token),
            timeout=30,
        )
        _raise_for_status(response, action="component prop configuration")
        response_payload = response.json() or {}
        if not isinstance(response_payload, Mapping):
            raise PipedreamTriggerSubscriptionError("Pipedream component configuration returned an invalid response.")
        errors = response_payload.get("errors")
        if isinstance(errors, list) and errors:
            raise PipedreamTriggerSubscriptionError(
                "Pipedream component configuration failed: " + "; ".join(str(error) for error in errors)
            )
        page_targets, prev_context = _coerce_target_options(response_payload)
        targets.extend(page_targets)
        targets = _unique_targets(targets, limit=limit)
        if len(targets) >= limit or not page_targets or not prev_context:
            break
    return targets


def _action_required_connect(agent: PersistentAgent, app_slug: str) -> EnsureSubscriptionResult:
    _session, connect_url = create_connect_session(agent, app_slug)
    if not connect_url:
        raise PipedreamTriggerSubscriptionError("Unable to create a Pipedream Connect link.")
    return EnsureSubscriptionResult(
        subscription=None,
        action_required=True,
        connect_url=connect_url,
        message=f"Authorization required. Please connect {app_slug} via: {connect_url}",
    )


def discover_targets(
    agent: PersistentAgent,
    *,
    app_slug: str,
    event_type: str,
    query: str = "",
    limit: int = 100,
) -> DiscoverTargetsResult:
    app = str(app_slug or "").strip().lower()
    event = str(event_type or "").strip().lower()
    component_id = _trigger_key_for(app, event)
    version = _trigger_version_for(app, event)
    prop_name = _target_prop_name_for(app, event)

    token = _get_pipedream_access_token()
    account_id = _active_account_id(agent, app, token)
    if not account_id:
        action_required = _action_required_connect(agent, app)
        return DiscoverTargetsResult(
            targets=[],
            action_required=True,
            connect_url=action_required.connect_url,
            message=action_required.message,
        )

    component = _retrieve_component_definition(
        token=token,
        component_id=component_id,
        version=version,
    )
    configured_props = _configured_props_for_account(
        component=component,
        app_slug=app,
        account_id=account_id,
    )
    targets = _configure_component_prop_options(
        token=token,
        component_id=component_id,
        version=version,
        external_user_id=str(agent.id),
        prop_name=prop_name,
        configured_props=configured_props,
        query=str(query or "").strip(),
        limit=max(1, min(int(limit or 100), 200)),
    )
    return DiscoverTargetsResult(
        targets=targets,
        message=(
            f"Found {len(targets)} Discord channel option(s)."
            if targets
            else "No Discord channels were returned by Pipedream."
        ),
    )


def _deploy_subscription(subscription: PersistentAgentPipedreamTriggerSubscription, token: str) -> None:
    payload = {
        "id": subscription.trigger_key,
        "external_user_id": subscription.external_user_id,
        "configured_props": subscription.configured_props,
        "webhook_url": _subscription_webhook_url(subscription),
        "emit_on_deploy": False,
    }
    if subscription.trigger_version:
        payload["version"] = subscription.trigger_version

    response = requests.post(
        f"{PIPEDREAM_API_BASE}/connect/{settings.PIPEDREAM_PROJECT_ID}/triggers/deploy",
        json=payload,
        headers=_pipedream_headers(token),
        timeout=30,
    )
    _raise_for_status(response, action="trigger deployment")
    data = (response.json() or {}).get("data") or {}
    deployed_trigger_id = str(data.get("id") or "")
    signing_key = str(data.get("webhook_signing_key") or "")
    if not deployed_trigger_id:
        raise PipedreamTriggerSubscriptionError("Pipedream did not return a deployed trigger id.")
    if not signing_key:
        raise PipedreamTriggerSubscriptionError("Pipedream did not return a trigger webhook signing key.")

    subscription.deployed_trigger_id = deployed_trigger_id
    subscription.signing_key = signing_key
    subscription.status = PersistentAgentPipedreamTriggerSubscription.Status.ACTIVE
    subscription.last_error = ""
    subscription.last_deployed_at = timezone.now()
    subscription.save(
        update_fields=[
            "deployed_trigger_id",
            "signing_key_encrypted",
            "status",
            "last_error",
            "last_deployed_at",
            "updated_at",
        ]
    )


def ensure_subscriptions(
    agent: PersistentAgent,
    *,
    app_slug: str,
    event_type: str,
    channel_ids: Iterable[object],
    channel_names: Mapping[str, object] | None = None,
) -> list[EnsureSubscriptionResult]:
    app = str(app_slug or "").strip().lower()
    event = str(event_type or "").strip().lower()
    trigger_key = _trigger_key_for(app, event)
    trigger_version = _trigger_version_for(app, event)

    normalized_channels = []
    seen_channels = set()
    for raw_channel_id in channel_ids or []:
        channel_id = _normalize_channel_id(raw_channel_id)
        if channel_id in seen_channels:
            continue
        seen_channels.add(channel_id)
        normalized_channels.append(channel_id)
    if not normalized_channels:
        raise PipedreamTriggerSubscriptionError("At least one Discord channel ID is required.")

    token = _get_pipedream_access_token()
    account_id = _active_account_id(agent, app, token)
    if not account_id:
        return [_action_required_connect(agent, app)]

    results: list[EnsureSubscriptionResult] = []
    for channel_id in normalized_channels:
        with transaction.atomic():
            existing = (
                PersistentAgentPipedreamTriggerSubscription.objects
                .select_for_update()
                .filter(
                    agent=agent,
                    app_slug=app,
                    event_type=event,
                    platform_channel=channel_id,
                    status=PersistentAgentPipedreamTriggerSubscription.Status.ACTIVE,
                )
                .first()
            )
            if existing and existing.deployed_trigger_id and existing.signing_key:
                results.append(
                    EnsureSubscriptionResult(
                        subscription=existing,
                        reused=True,
                        message=f"Already subscribed to Discord channel {channel_id}.",
                    )
                )
                continue

            subscription = existing or PersistentAgentPipedreamTriggerSubscription(
                agent=agent,
                app_slug=app,
                event_type=event,
                platform_channel=channel_id,
                trigger_key=trigger_key,
                trigger_version=trigger_version,
                external_user_id=str(agent.id),
            )
            subscription.platform_channel_name = _channel_name_for(channel_id, channel_names)
            subscription.configured_props = _discord_configured_props(channel_id, account_id)
            subscription.status = PersistentAgentPipedreamTriggerSubscription.Status.ACTIVE
            subscription.last_error = ""
            subscription.save()

        try:
            _deploy_subscription(subscription, token)
            results.append(
                EnsureSubscriptionResult(
                    subscription=subscription,
                    created=True,
                    message=f"Subscribed to Discord channel {channel_id}.",
                )
            )
        except requests.RequestException as exc:
            subscription.record_error(str(exc))
            raise PipedreamTriggerSubscriptionError(f"Pipedream trigger deployment failed: {exc}") from exc
        except PipedreamTriggerSubscriptionError as exc:
            subscription.record_error(str(exc))
            raise

    return results


def list_subscriptions(agent: PersistentAgent) -> list[dict[str, object]]:
    return [
        serialize_subscription(subscription)
        for subscription in agent.pipedream_trigger_subscriptions.order_by("app_slug", "event_type", "platform_channel")
    ]


def disable_subscription(agent: PersistentAgent, subscription_id: str) -> dict[str, object]:
    subscription = agent.pipedream_trigger_subscriptions.get(id=subscription_id)
    if subscription.deployed_trigger_id:
        token = _get_pipedream_access_token()
        response = requests.delete(
            f"{PIPEDREAM_API_BASE}/connect/{settings.PIPEDREAM_PROJECT_ID}/deployed-triggers/{subscription.deployed_trigger_id}",
            params={
                "external_user_id": subscription.external_user_id,
                "ignore_hook_errors": "true",
            },
            headers=_pipedream_headers(token),
            timeout=20,
        )
        _raise_for_status(response, action="trigger deletion")

    subscription.status = PersistentAgentPipedreamTriggerSubscription.Status.DISABLED
    subscription.last_error = ""
    subscription.save(update_fields=["status", "last_error", "updated_at"])
    return serialize_subscription(subscription)


def serialize_subscription(subscription: PersistentAgentPipedreamTriggerSubscription) -> dict[str, object]:
    return {
        "id": str(subscription.id),
        "app_slug": subscription.app_slug,
        "event_type": subscription.event_type,
        "platform_channel": subscription.platform_channel,
        "platform_channel_name": subscription.platform_channel_name,
        "trigger_key": subscription.trigger_key,
        "trigger_version": subscription.trigger_version,
        "deployed_trigger_id": subscription.deployed_trigger_id,
        "configured_props": subscription.configured_props,
        "status": subscription.status,
        "last_error": subscription.last_error,
        "last_event_at": subscription.last_event_at.isoformat() if subscription.last_event_at else None,
        "last_deployed_at": subscription.last_deployed_at.isoformat() if subscription.last_deployed_at else None,
    }


def verify_pipedream_signature(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    signature_header: str,
    raw_body: bytes,
) -> None:
    signing_key = subscription.signing_key
    if not signing_key:
        raise PipedreamTriggerSignatureError("Missing signing key.")
    if not signature_header:
        raise PipedreamTriggerSignatureError("Missing Pipedream signature.")

    parts = {}
    for item in signature_header.split(","):
        key, sep, value = item.partition("=")
        if sep:
            parts[key.strip()] = value.strip()
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise PipedreamTriggerSignatureError("Malformed Pipedream signature.")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise PipedreamTriggerSignatureError("Invalid Pipedream signature timestamp.") from exc
    if abs(int(time.time()) - signed_at) > SIGNATURE_TOLERANCE_SECONDS:
        raise PipedreamTriggerSignatureError("Expired Pipedream signature.")

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected = hmac.new(signing_key.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected, signature):
        raise PipedreamTriggerSignatureError("Invalid Pipedream signature.")


def _coerce_json_body(raw_body: bytes) -> dict[str, object]:
    try:
        parsed = json.loads((raw_body or b"{}").decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON payload.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Pipedream trigger payload must be a JSON object.")
    return parsed


def _pipedream_event_from_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    event = payload.get("event")
    if isinstance(event, Mapping):
        return event
    return payload


def _event_value(event: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = event.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _discord_author(event: Mapping[str, object]) -> tuple[str, str]:
    author = event.get("author")
    if isinstance(author, Mapping):
        author_id = _event_value(author, "id", "userId", "user_id")
        username = _event_value(author, "global_name", "username", "displayName", "name")
        if author_id or username:
            return author_id, username
    return _event_value(event, "authorId", "author_id", "userId", "user_id"), _event_value(
        event,
        "username",
        "displayName",
        "user",
    )


def _discord_channel_address(guild_id: str, channel_id: str) -> str:
    guild_part = guild_id or "unknown"
    return f"discord://guild/{guild_part}/channel/{channel_id}"


def _discord_agent_address(agent_id: object) -> str:
    return f"discord://agent/{agent_id}"


def _ensure_discord_agent_endpoint(agent: PersistentAgent) -> None:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.DISCORD,
        address=_discord_agent_address(agent.id),
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


def _discord_message_body(event: Mapping[str, object]) -> str:
    return _event_value(event, "content", "message", "text", "body")


def _normalize_discord_event(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    payload: Mapping[str, object],
) -> tuple[ParsedMessage, str]:
    event = _pipedream_event_from_payload(payload)
    message_id = _event_value(event, "id", "messageId", "message_id")
    channel_id = _event_value(event, "channelID", "channelId", "channel_id") or subscription.platform_channel
    if channel_id != subscription.platform_channel:
        raise ValueError("Discord event channel does not match this subscription.")
    body = _discord_message_body(event)
    if not message_id or not body:
        raise ValueError("Discord message event is missing a message id or content.")

    guild_id = _event_value(event, "guildID", "guildId", "guild_id")
    channel_name = _event_value(event, "channelName", "channel_name") or subscription.platform_channel_name
    guild_name = _event_value(event, "guildName", "guild_name")
    author_id, author_name = _discord_author(event)
    source_label_parts = []
    if author_name:
        source_label_parts.append(author_name)
    if channel_name:
        source_label_parts.append(f"#{channel_name.lstrip('#')}")
    source_label = " in ".join(source_label_parts) if source_label_parts else channel_name or channel_id

    attachments = event.get("attachments")
    normalized_payload = {
        "source": "pipedream_trigger",
        "source_kind": "discord",
        "source_label": source_label,
        "app_slug": subscription.app_slug,
        "event_type": subscription.event_type,
        "subscription_id": str(subscription.id),
        "deployed_trigger_id": subscription.deployed_trigger_id,
        "discord_message_id": message_id,
        "discord_channel_id": channel_id,
        "discord_channel_name": channel_name,
        "discord_guild_id": guild_id,
        "discord_guild_name": guild_name,
        "discord_author_id": author_id,
        "discord_author_name": author_name,
        "discord_attachments": attachments if isinstance(attachments, list) else [],
        "pipedream_payload": dict(payload),
    }
    parsed = ParsedMessage(
        sender=_discord_channel_address(guild_id, channel_id),
        recipient=_discord_agent_address(subscription.agent_id),
        subject=None,
        body=body,
        attachments=[],
        raw_payload=normalized_payload,
        msg_channel=CommsChannel.DISCORD.value,
    )
    display_name = f"#{channel_name.lstrip('#')}" if channel_name else f"Discord {channel_id}"
    return parsed, display_name


def ingest_trigger_delivery(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    raw_body: bytes,
) -> dict[str, object]:
    if subscription.app_slug != DISCORD_APP_SLUG or subscription.event_type != DISCORD_MESSAGE_EVENT_TYPE:
        raise ValueError("Unsupported Pipedream trigger subscription.")

    payload = _coerce_json_body(raw_body)
    parsed, display_name = _normalize_discord_event(subscription, payload)
    _ensure_discord_agent_endpoint(subscription.agent)
    info = ingest_inbound_message(CommsChannel.DISCORD, parsed, filespace_import_mode="sync")
    if info.message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=info.message.conversation_id).update(display_name=display_name)
    subscription.record_event()
    return {
        "message_id": str(info.message.id),
        "conversation_id": str(info.message.conversation_id) if info.message.conversation_id else "",
    }
