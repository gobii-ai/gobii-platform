"""Pipedream Connect trigger subscription provisioning and ingestion."""

import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Mapping

import redis
import requests
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.tools.mcp_manager import get_mcp_manager
from config.redis_client import get_redis_client
from api.integrations.pipedream_connect import create_connect_session
from api.models import (
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentPipedreamTriggerSubscription,
)

PIPEDREAM_API_BASE = "https://api.pipedream.com/v1"
DISCORD_APP_SLUG = "discord"
DISCORD_MESSAGE_EVENT_TYPE = "message.created"
DISCORD_MESSAGE_TRIGGER_KEY = "discord-new-message"
DISCORD_MESSAGE_TRIGGER_VERSION = "1.0.3"
DISCORD_SEND_TOOL_NAMES = {
    "discord-send-message",
    "discord-send-message-advanced",
    "discord-send-message-with-file",
}
SIGNATURE_TOLERANCE_SECONDS = 300
DISCORD_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")
DISCORD_INBOUND_DEBOUNCE_DEADLINE_KEY = "agent:discord-inbound-debounce:{agent_id}:deadline"
DISCORD_INBOUND_DEBOUNCE_SCHEDULED_KEY = "agent:discord-inbound-debounce:{agent_id}:scheduled"

logger = logging.getLogger(__name__)


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


def _nested_mapping(root: Mapping[str, object], *keys: str) -> Mapping[str, object] | None:
    current: object = root
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, Mapping) else None


def _discord_message_event(payload: Mapping[str, object]) -> Mapping[str, object]:
    event = _pipedream_event_from_payload(payload)
    for key in ("message", "payload", "data"):
        nested = event.get(key)
        if isinstance(nested, Mapping) and (
            _event_value(nested, "content", "message", "text", "body")
            or _event_value(nested, "id", "messageId", "message_id")
        ):
            return nested
    return event


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
    author_candidates = [
        event.get("author"),
        event.get("user"),
        _nested_mapping(event, "member", "user"),
        _nested_mapping(event, "message", "author"),
        _nested_mapping(event, "payload", "author"),
        _nested_mapping(event, "data", "author"),
    ]
    for author in author_candidates:
        if not isinstance(author, Mapping):
            continue
        author_id = _event_value(author, "id", "userId", "user_id")
        username = _event_value(
            author,
            "global_name",
            "globalName",
            "display_name",
            "displayName",
            "nick",
            "username",
            "name",
        )
        if author_id or username:
            return author_id, username

    member = event.get("member")
    if isinstance(member, Mapping):
        member_name = _event_value(member, "nick", "display_name", "displayName")
        user = member.get("user")
        if member_name and isinstance(user, Mapping):
            return _event_value(user, "id", "userId", "user_id"), member_name

    author_name = _event_value(
        event,
        "author",
        "global_name",
        "globalName",
        "username",
        "display_name",
        "displayName",
        "user",
    )
    return _event_value(event, "authorID", "authorId", "author_id", "userID", "userId", "user_id"), author_name


def _discord_channel_address(guild_id: str, channel_id: str) -> str:
    guild_part = guild_id or "unknown"
    return f"discord://guild/{guild_part}/channel/{channel_id}"


def _discord_conversation_address(agent_id: object, guild_id: str, channel_id: str) -> str:
    guild_part = guild_id or "unknown"
    return f"discord://agent/{agent_id}/guild/{guild_part}/channel/{channel_id}"


def _discord_agent_address(agent_id: object) -> str:
    return f"discord://agent/{agent_id}"


def _ensure_discord_agent_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
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
    return endpoint


def _ensure_conversation_participant(
    conversation: PersistentAgentConversation,
    endpoint: PersistentAgentCommsEndpoint,
    role: str,
) -> None:
    PersistentAgentConversationParticipant.objects.get_or_create(
        conversation=conversation,
        endpoint=endpoint,
        defaults={"role": role},
    )


def _display_name_for_channel(channel_id: str, channel_name: str = "") -> str:
    return f"#{channel_name.lstrip('#')}" if channel_name else f"Discord {channel_id}"


def _discord_channel_source_label(channel_id: str, channel_name: str = "") -> str:
    return _display_name_for_channel(channel_id, channel_name)


def _discord_conversation_address_for_channel(agent: PersistentAgent, channel_id: str) -> str:
    existing = (
        PersistentAgentConversation.objects
        .filter(
            owner_agent=agent,
            channel=CommsChannel.DISCORD,
            address__startswith=f"discord://agent/{agent.id}/",
            address__endswith=f"/channel/{channel_id}",
        )
        .order_by("-id")
        .first()
    )
    if existing:
        return existing.address
    return _discord_conversation_address(agent.id, "unknown", channel_id)


def _get_or_create_discord_conversation(
    agent: PersistentAgent,
    *,
    address: str,
    channel_id: str,
    channel_name: str = "",
) -> PersistentAgentConversation:
    display_name = _display_name_for_channel(channel_id, channel_name)
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


def _discord_channel_endpoint(address: str) -> PersistentAgentCommsEndpoint:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.DISCORD,
        address=address,
        defaults={"owner_agent": None},
    )
    return endpoint


def _ensure_discord_conversation_participants(
    agent: PersistentAgent,
    conversation: PersistentAgentConversation,
    *,
    platform_channel_address: str,
) -> tuple[PersistentAgentCommsEndpoint, PersistentAgentCommsEndpoint]:
    from_endpoint = _ensure_discord_agent_endpoint(agent)
    channel_endpoint = _discord_channel_endpoint(platform_channel_address)
    _ensure_conversation_participant(
        conversation,
        from_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.AGENT,
    )
    _ensure_conversation_participant(
        conversation,
        channel_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
    )
    return from_endpoint, channel_endpoint


def _find_recent_discord_outbound(
    agent: PersistentAgent,
    *,
    channel_id: str,
    body: str,
) -> PersistentAgentMessage | None:
    cutoff = timezone.now() - timedelta(minutes=10)
    return (
        PersistentAgentMessage.objects
        .select_related("conversation")
        .filter(
            owner_agent=agent,
            is_outbound=True,
            conversation__channel=CommsChannel.DISCORD,
            body=body,
            timestamp__gte=cutoff,
            raw_payload__discord_channel_id=channel_id,
            raw_payload__source="pipedream_tool",
        )
        .order_by("-timestamp")
        .first()
    )


def _create_discord_outbound_message(
    agent: PersistentAgent,
    *,
    channel_id: str,
    body: str,
    conversation_address: str,
    platform_channel_address: str = "",
    channel_name: str = "",
    raw_payload: Mapping[str, object] | None = None,
) -> PersistentAgentMessage:
    conversation = _get_or_create_discord_conversation(
        agent,
        address=conversation_address,
        channel_id=channel_id,
        channel_name=channel_name,
    )
    from_endpoint, channel_endpoint = _ensure_discord_conversation_participants(
        agent,
        conversation,
        platform_channel_address=platform_channel_address or _discord_channel_address("", channel_id),
    )
    now = timezone.now()
    payload = dict(raw_payload or {})
    payload.setdefault("source", "pipedream_tool")
    payload.setdefault("source_kind", "discord")
    payload.setdefault("app_slug", DISCORD_APP_SLUG)
    payload.setdefault("event_type", DISCORD_MESSAGE_EVENT_TYPE)
    payload.setdefault("discord_channel_id", channel_id)
    payload.setdefault("discord_channel_name", channel_name)
    payload.setdefault("discord_platform_channel_address", channel_endpoint.address)
    payload.setdefault("discord_conversation_address", conversation.address)
    payload.setdefault("source_label", _discord_channel_source_label(channel_id, channel_name))
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


def record_discord_outbound_send(
    agent: PersistentAgent,
    *,
    tool_name: str,
    params: Mapping[str, object],
    result: Mapping[str, object] | None = None,
) -> PersistentAgentMessage | None:
    if tool_name not in DISCORD_SEND_TOOL_NAMES:
        return None
    channel_id = _event_value(params, "channel", "channelId", "channel_id")
    body = _event_value(params, "message", "content", "text", "body")
    if not channel_id or not DISCORD_SNOWFLAKE_RE.fullmatch(channel_id) or not body:
        return None
    existing = _find_recent_discord_outbound(agent, channel_id=channel_id, body=body)
    if existing:
        return existing
    subscription = (
        agent.pipedream_trigger_subscriptions
        .filter(
            app_slug=DISCORD_APP_SLUG,
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
            platform_channel=channel_id,
            status=PersistentAgentPipedreamTriggerSubscription.Status.ACTIVE,
        )
        .order_by("-updated_at")
        .first()
    )
    channel_name = subscription.platform_channel_name if subscription else ""
    raw_payload = {
        "source": "pipedream_tool",
        "source_kind": "discord",
        "app_slug": DISCORD_APP_SLUG,
        "event_type": DISCORD_MESSAGE_EVENT_TYPE,
        "discord_channel_id": channel_id,
        "discord_channel_name": channel_name,
        "source_label": _discord_channel_source_label(channel_id, channel_name),
        "pipedream_tool_name": tool_name,
        "pipedream_tool_params": dict(params),
        "pipedream_tool_result": dict(result or {}),
    }
    return _create_discord_outbound_message(
        agent,
        channel_id=channel_id,
        body=body,
        conversation_address=_discord_conversation_address_for_channel(agent, channel_id),
        channel_name=channel_name,
        raw_payload=raw_payload,
    )


def _discord_message_body(event: Mapping[str, object]) -> str:
    return _event_value(event, "content", "message", "text", "body")


def _event_list(event: Mapping[str, object], key: str) -> list[object]:
    value = event.get(key)
    return value if isinstance(value, list) else []


def _normalize_discord_event(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    payload: Mapping[str, object],
) -> tuple[ParsedMessage, str]:
    event = _discord_message_event(payload)
    message_id = _event_value(event, "id", "messageId", "message_id")
    channel_id = _event_value(event, "channelID", "channelId", "channel_id") or subscription.platform_channel
    if channel_id != subscription.platform_channel:
        raise ValueError("Discord event channel does not match this subscription.")
    body = _discord_message_body(event)
    attachments = _event_list(event, "attachments")
    embeds = _event_list(event, "embeds")
    if not message_id or (not body and not attachments and not embeds):
        raise ValueError("Discord message event is missing a message id, content, attachments, or embeds.")

    guild_id = _event_value(event, "guildID", "guildId", "guild_id")
    channel_name = _event_value(event, "channelName", "channel_name") or subscription.platform_channel_name
    guild_name = _event_value(event, "guildName", "guild_name")
    author_id, author_name = _discord_author(event)
    platform_channel_address = _discord_channel_address(guild_id, channel_id)
    conversation_address = _discord_conversation_address(subscription.agent_id, guild_id, channel_id)
    source_label_parts = []
    if author_name:
        source_label_parts.append(author_name)
    if channel_name:
        source_label_parts.append(f"#{channel_name.lstrip('#')}")
    source_label = " in ".join(source_label_parts) if source_label_parts else channel_name or channel_id

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
        "discord_attachments": attachments,
        "discord_embeds": embeds,
        "discord_platform_channel_address": platform_channel_address,
        "discord_conversation_address": conversation_address,
        "pipedream_payload": dict(payload),
    }
    parsed = ParsedMessage(
        sender=platform_channel_address,
        recipient=_discord_agent_address(subscription.agent_id),
        subject=None,
        body=body,
        attachments=[],
        raw_payload=normalized_payload,
        msg_channel=CommsChannel.DISCORD.value,
        conversation_address=conversation_address,
    )
    display_name = f"#{channel_name.lstrip('#')}" if channel_name else f"Discord {channel_id}"
    return parsed, display_name


def _discord_event_is_bot_authored(raw_payload: Mapping[str, object]) -> bool:
    pipedream_payload = raw_payload.get("pipedream_payload")
    if not isinstance(pipedream_payload, Mapping):
        return False
    metadata = pipedream_payload.get("author_metadata")
    if isinstance(metadata, Mapping) and metadata.get("bot") is True:
        return True
    return bool(_event_value(pipedream_payload, "webhookId", "webhookID", "webhook_id"))


def _merge_discord_echo_into_outbound(
    message: PersistentAgentMessage,
    *,
    parsed: ParsedMessage,
    display_name: str,
) -> PersistentAgentMessage:
    raw_payload = parsed.raw_payload if isinstance(parsed.raw_payload, Mapping) else {}
    channel_id = str(raw_payload.get("discord_channel_id") or "").strip()
    channel_name = str(raw_payload.get("discord_channel_name") or "").strip()
    guild_id = str(raw_payload.get("discord_guild_id") or "").strip()
    platform_channel_address = str(raw_payload.get("discord_platform_channel_address") or "").strip()
    if guild_id and channel_id:
        address = _discord_conversation_address(message.owner_agent_id, guild_id, channel_id)
        if not platform_channel_address:
            platform_channel_address = _discord_channel_address(guild_id, channel_id)
        conversation = _get_or_create_discord_conversation(
            message.owner_agent,
            address=address,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        _ensure_discord_conversation_participants(
            message.owner_agent,
            conversation,
            platform_channel_address=platform_channel_address,
        )
        if message.conversation_id != conversation.id:
            message.conversation = conversation
    next_payload = dict(message.raw_payload or {})
    next_payload.update(
        {
            "discord_message_id": raw_payload.get("discord_message_id", ""),
            "discord_channel_id": raw_payload.get("discord_channel_id", ""),
            "discord_channel_name": raw_payload.get("discord_channel_name", ""),
            "discord_guild_id": raw_payload.get("discord_guild_id", ""),
            "discord_guild_name": raw_payload.get("discord_guild_name", ""),
            "discord_author_id": raw_payload.get("discord_author_id", ""),
            "discord_author_name": raw_payload.get("discord_author_name", ""),
            "discord_attachments": raw_payload.get("discord_attachments", []),
            "discord_platform_channel_address": platform_channel_address,
            "discord_conversation_address": message.conversation.address if message.conversation_id else "",
            "source_label": _discord_channel_source_label(channel_id, channel_name),
            "pipedream_trigger_echo_payload": raw_payload.get("pipedream_payload", {}),
        }
    )
    message.raw_payload = next_payload
    message.latest_status = DeliveryStatus.SENT
    if message.latest_sent_at is None:
        message.latest_sent_at = timezone.now()
    message.save(update_fields=["conversation", "raw_payload", "latest_status", "latest_sent_at"])
    if message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=message.conversation_id).update(display_name=display_name)
    return message


def _upsert_discord_outbound_echo(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    *,
    parsed: ParsedMessage,
    display_name: str,
) -> PersistentAgentMessage | None:
    raw_payload = parsed.raw_payload if isinstance(parsed.raw_payload, Mapping) else {}
    channel_id = str(raw_payload.get("discord_channel_id") or "").strip()
    body = parsed.body or ""
    if not channel_id or not body:
        return None
    recent_outbound = _find_recent_discord_outbound(subscription.agent, channel_id=channel_id, body=body)
    bot_authored = _discord_event_is_bot_authored(raw_payload)
    author_name = str(raw_payload.get("discord_author_name") or "").strip()
    is_named_agent = bool(author_name and author_name == subscription.agent.name)
    if not recent_outbound and not (bot_authored and is_named_agent):
        return None
    if recent_outbound:
        return _merge_discord_echo_into_outbound(recent_outbound, parsed=parsed, display_name=display_name)

    channel_name = str(raw_payload.get("discord_channel_name") or "").strip()
    outbound_payload = {
        "source": "pipedream_tool",
        "source_kind": "discord",
        "app_slug": subscription.app_slug,
        "event_type": subscription.event_type,
        "discord_channel_id": channel_id,
        "discord_channel_name": channel_name,
        "source_label": _discord_channel_source_label(channel_id, channel_name),
        "discord_message_id": raw_payload.get("discord_message_id", ""),
        "discord_guild_id": raw_payload.get("discord_guild_id", ""),
        "discord_guild_name": raw_payload.get("discord_guild_name", ""),
        "discord_author_id": raw_payload.get("discord_author_id", ""),
        "discord_author_name": raw_payload.get("discord_author_name", ""),
        "discord_attachments": raw_payload.get("discord_attachments", []),
        "discord_platform_channel_address": raw_payload.get("discord_platform_channel_address", ""),
        "discord_conversation_address": raw_payload.get("discord_conversation_address", ""),
        "pipedream_trigger_echo_payload": raw_payload.get("pipedream_payload", {}),
    }
    return _create_discord_outbound_message(
        subscription.agent,
        channel_id=channel_id,
        body=body,
        conversation_address=parsed.conversation_address or parsed.sender,
        platform_channel_address=str(raw_payload.get("discord_platform_channel_address") or parsed.sender),
        channel_name=channel_name,
        raw_payload=outbound_payload,
    )


def _discord_inbound_debounce_seconds() -> int:
    return max(0, int(settings.PIPEDREAM_DISCORD_INBOUND_DEBOUNCE_SECONDS))


def _discord_inbound_debounce_keys(agent_id: str) -> tuple[str, str]:
    return (
        DISCORD_INBOUND_DEBOUNCE_DEADLINE_KEY.format(agent_id=agent_id),
        DISCORD_INBOUND_DEBOUNCE_SCHEDULED_KEY.format(agent_id=agent_id),
    )


def _discord_inbound_debounce_ttl(delay_seconds: int) -> int:
    return max(60, delay_seconds * 6)


def _process_agent_events_after_discord_debounce(agent_id: str, *, countdown: int = 0) -> None:
    from api.agent.tasks import process_agent_events_task

    if countdown > 0:
        process_agent_events_task.apply_async(args=[agent_id], countdown=countdown)
    else:
        process_agent_events_task.delay(agent_id)


def schedule_discord_inbound_processing(agent_id: str) -> dict[str, object]:
    debounce_seconds = _discord_inbound_debounce_seconds()
    if debounce_seconds <= 0:
        _process_agent_events_after_discord_debounce(str(agent_id))
        return {"debounced": False, "debounce_seconds": 0, "scheduled": True}

    normalized_agent_id = str(agent_id)
    deadline_key, scheduled_key = _discord_inbound_debounce_keys(normalized_agent_id)
    deadline = time.time() + debounce_seconds
    ttl = _discord_inbound_debounce_ttl(debounce_seconds)

    try:
        redis_client = get_redis_client()
        redis_client.set(deadline_key, f"{deadline:.6f}", ex=ttl)
        scheduled = bool(redis_client.set(scheduled_key, "1", ex=ttl, nx=True))
    except redis.exceptions.RedisError:
        logger.exception(
            "Failed scheduling Discord inbound debounce for agent %s; falling back to delayed processing.",
            normalized_agent_id,
        )
        _process_agent_events_after_discord_debounce(normalized_agent_id, countdown=debounce_seconds)
        return {
            "debounced": False,
            "debounce_seconds": debounce_seconds,
            "scheduled": True,
            "fallback": True,
        }

    if scheduled:
        if settings.CELERY_TASK_ALWAYS_EAGER:
            redis_client.delete(deadline_key, scheduled_key)
            _process_agent_events_after_discord_debounce(normalized_agent_id)
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


def _coerce_redis_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def process_discord_inbound_debounce(agent_id: str) -> None:
    debounce_seconds = _discord_inbound_debounce_seconds()
    normalized_agent_id = str(agent_id)
    if debounce_seconds <= 0:
        _process_agent_events_after_discord_debounce(normalized_agent_id)
        return

    deadline_key, scheduled_key = _discord_inbound_debounce_keys(normalized_agent_id)
    now = time.time()

    try:
        redis_client = get_redis_client()
        deadline = _coerce_redis_float(redis_client.get(deadline_key))
        if deadline is not None and deadline > now:
            if settings.CELERY_TASK_ALWAYS_EAGER:
                redis_client.delete(deadline_key, scheduled_key)
                _process_agent_events_after_discord_debounce(normalized_agent_id)
                return

            countdown = max(1, math.ceil(deadline - now))
            ttl = _discord_inbound_debounce_ttl(max(debounce_seconds, countdown))
            redis_client.expire(deadline_key, ttl)
            redis_client.expire(scheduled_key, ttl)
            from api.agent.tasks.process_events import process_discord_inbound_debounce_task

            process_discord_inbound_debounce_task.apply_async(
                args=[normalized_agent_id],
                countdown=countdown,
            )
            return

        redis_client.delete(deadline_key, scheduled_key)
    except redis.exceptions.RedisError:
        logger.exception(
            "Failed processing Discord inbound debounce for agent %s; falling back to immediate processing.",
            normalized_agent_id,
        )

    _process_agent_events_after_discord_debounce(normalized_agent_id)


def ingest_trigger_delivery(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    raw_body: bytes,
) -> dict[str, object]:
    if subscription.app_slug != DISCORD_APP_SLUG or subscription.event_type != DISCORD_MESSAGE_EVENT_TYPE:
        raise ValueError("Unsupported Pipedream trigger subscription.")

    payload = _coerce_json_body(raw_body)
    parsed, display_name = _normalize_discord_event(subscription, payload)
    outbound_echo = _upsert_discord_outbound_echo(subscription, parsed=parsed, display_name=display_name)
    if outbound_echo:
        subscription.record_event()
        return {
            "message_id": str(outbound_echo.id),
            "conversation_id": str(outbound_echo.conversation_id) if outbound_echo.conversation_id else "",
            "ignored": True,
            "outbound_echo": True,
        }

    _ensure_discord_agent_endpoint(subscription.agent)
    info = ingest_inbound_message(
        CommsChannel.DISCORD,
        parsed,
        filespace_import_mode="sync",
        trigger_processing=False,
    )
    if info.message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=info.message.conversation_id).update(display_name=display_name)
    debounce_result = schedule_discord_inbound_processing(str(subscription.agent_id))
    subscription.record_event()
    return {
        "message_id": str(info.message.id),
        "conversation_id": str(info.message.conversation_id) if info.message.conversation_id else "",
        "debounced": bool(debounce_result.get("debounced")),
        "debounce_seconds": debounce_result.get("debounce_seconds", 0),
    }
