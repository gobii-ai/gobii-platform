"""Native Slack integration."""

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentSlackChannelSubscription,
    PersistentAgentSlackEventReceipt,
    PersistentAgentSlackWorkspace,
    PersistentAgentSystemSkillState,
)
from api.agent.system_skills.defaults import SLACK_NATIVE_SYSTEM_SKILL_KEY
from api.services.agent_avatar_public import build_public_agent_avatar_thumbnail_url
from api.services.native_integrations import (
    SLACK_PROVIDER,
    NativeIntegrationAuthError,
    get_native_integration_secret,
    load_native_integration_credentials,
    native_integration_setup_url,
    refresh_oauth_credentials_if_needed,
)
from api.services.persistent_agent_secrets import resolve_global_secret_owner_for_agent
from api.services.slack_messages import (
    create_slack_outbound_message,
    ensure_slack_agent_endpoint,
    schedule_slack_inbound_processing,
    slack_agent_address,
    slack_channel_address,
    slack_channel_source_label,
    slack_conversation_address,
)
from util.text_sanitizer import decode_unicode_character_escapes

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"
SLACK_PUBLIC_CHANNEL_TYPE = "public_channel"
SLACK_PRIVATE_CHANNEL_TYPE = "private_channel"
SLACK_SUPPORTED_CHANNEL_TYPES = {SLACK_PUBLIC_CHANNEL_TYPE, SLACK_PRIVATE_CHANNEL_TYPE}
SLACK_SIGNATURE_MAX_AGE_SECONDS = 60 * 5


class SlackIntegrationError(RuntimeError):
    """Raised when native Slack setup or delivery cannot continue."""


@dataclass(frozen=True)
class SlackEventMessage:
    event_id: str
    team_id: str
    channel_id: str
    channel_name: str
    channel_type: str
    user_id: str
    text: str
    ts: str
    thread_ts: str
    raw_event: Mapping[str, Any]


def _agent_owner(agent: PersistentAgent) -> tuple[Any, Any]:
    if agent.organization_id:
        return None, agent.organization
    return agent.user, None


def _claimed_workspace_queryset(agent: PersistentAgent):
    owner_user, organization = _agent_owner(agent)
    queryset = PersistentAgentSlackWorkspace.objects.filter(is_active=True)
    if organization is not None:
        return queryset.filter(organization=organization)
    return queryset.filter(owner_user=owner_user)


def _owner_matches_slack_workspace_claim(
    workspace: PersistentAgentSlackWorkspace,
    *,
    owner_user,
    owner_org,
) -> bool:
    return workspace.owner_user_id == getattr(owner_user, "id", None) and workspace.organization_id == getattr(owner_org, "id", None)


def _update_slack_workspace_claim(
    workspace: PersistentAgentSlackWorkspace,
    defaults: Mapping[str, Any],
) -> PersistentAgentSlackWorkspace:
    updates = []
    for field, value in defaults.items():
        if getattr(workspace, field) != value:
            setattr(workspace, field, value)
            updates.append(field)
    if updates:
        updates.append("updated_at")
        workspace.save(update_fields=updates)
    return workspace


def sync_slack_workspace_from_oauth(
    token_payload: Mapping[str, Any],
    *,
    owner_user,
    owner_org,
    claimed_by,
) -> PersistentAgentSlackWorkspace:
    team = token_payload.get("team") if isinstance(token_payload.get("team"), Mapping) else {}
    enterprise = token_payload.get("enterprise") if isinstance(token_payload.get("enterprise"), Mapping) else {}
    team_id = str(team.get("id") or token_payload.get("team_id") or "").strip()
    if not team_id:
        raise ValidationError({"team_id": "Slack OAuth did not return a team ID."})

    defaults = {
        "team_name": str(team.get("name") or team_id)[:255],
        "enterprise_id": str(enterprise.get("id") or "")[:64],
        "enterprise_name": str(enterprise.get("name") or "")[:255],
        "app_id": str(token_payload.get("app_id") or "")[:64],
        "bot_user_id": str(token_payload.get("bot_user_id") or "")[:64],
        "owner_user": owner_user,
        "organization": owner_org,
        "claimed_by": claimed_by if getattr(claimed_by, "is_authenticated", False) else None,
        "is_active": True,
        "last_synced_at": timezone.now(),
    }

    existing = PersistentAgentSlackWorkspace.objects.select_for_update().filter(team_id=team_id, is_active=True).first()
    if existing:
        if not _owner_matches_slack_workspace_claim(existing, owner_user=owner_user, owner_org=owner_org):
            raise ValidationError({"team_id": "This Slack workspace is already connected by another owner."})
        return _update_slack_workspace_claim(existing, defaults)

    try:
        return PersistentAgentSlackWorkspace.objects.create(team_id=team_id, **defaults)
    except IntegrityError as exc:
        existing = PersistentAgentSlackWorkspace.objects.select_for_update().filter(team_id=team_id, is_active=True).first()
        if not existing or not _owner_matches_slack_workspace_claim(existing, owner_user=owner_user, owner_org=owner_org):
            raise ValidationError({"team_id": "This Slack workspace is already connected by another owner."}) from exc
        return _update_slack_workspace_claim(existing, defaults)


def disconnect_slack_native_integration(*, owner_user=None, organization=None) -> dict[str, int]:
    if (owner_user is None) == (organization is None):
        raise ValueError("Exactly one Slack owner must be provided.")

    now = timezone.now()
    with transaction.atomic():
        workspace_queryset = PersistentAgentSlackWorkspace.objects.select_for_update()
        agent_queryset = PersistentAgent.objects.non_eval().alive()
        if organization is not None:
            workspace_queryset = workspace_queryset.filter(organization=organization)
            agent_queryset = agent_queryset.filter(organization=organization)
        else:
            workspace_queryset = workspace_queryset.filter(owner_user=owner_user)
            agent_queryset = agent_queryset.filter(user=owner_user, organization_id__isnull=True)

        workspace_ids = list(workspace_queryset.filter(is_active=True).values_list("id", flat=True))
        agent_ids = list(agent_queryset.values_list("id", flat=True))
        subscription_count = 0
        workspace_count = 0
        if workspace_ids:
            subscription_count = PersistentAgentSlackChannelSubscription.objects.filter(workspace_id__in=workspace_ids).exclude(
                status=PersistentAgentSlackChannelSubscription.Status.DISABLED
            ).update(status=PersistentAgentSlackChannelSubscription.Status.DISABLED, updated_at=now)
            workspace_count = PersistentAgentSlackWorkspace.objects.filter(id__in=workspace_ids).update(
                is_active=False,
                last_synced_at=now,
                updated_at=now,
            )
        skill_count = PersistentAgentSystemSkillState.objects.filter(
            agent_id__in=agent_ids,
            skill_key=SLACK_NATIVE_SYSTEM_SKILL_KEY,
            is_enabled=True,
        ).update(is_enabled=False)

    return {
        "workspaces_disconnected": workspace_count,
        "subscriptions_disabled": subscription_count,
        "agents_disabled": skill_count,
    }


def slack_setup_required_response(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "status": "action_required",
        "message": (
            "Connect Slack to Gobii, then choose channels for this agent. "
            "Slack supports per-message display names and avatars, not separate mentionable bot users per agent."
        ),
        "setup_url": native_integration_setup_url(),
        "channels": [],
    }


def _owner_credentials(agent: PersistentAgent) -> dict[str, Any]:
    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    secret = get_native_integration_secret(SLACK_PROVIDER.key, owner_user, owner_org)
    if secret is None:
        raise SlackIntegrationError(slack_setup_required_response(agent)["message"])
    try:
        credentials = load_native_integration_credentials(secret)
        return refresh_oauth_credentials_if_needed(SLACK_PROVIDER, secret, credentials)
    except NativeIntegrationAuthError as exc:
        raise SlackIntegrationError(str(exc)) from exc


def _slack_headers(agent: PersistentAgent) -> dict[str, str]:
    credentials = _owner_credentials(agent)
    access_token = str(credentials.get("access_token") or "")
    if not access_token:
        raise SlackIntegrationError("Slack must be reconnected.")
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _raise_for_slack_response(response: requests.Response, *, action: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise SlackIntegrationError(f"Slack {action} failed with HTTP {response.status_code}.") from exc
    try:
        payload = response.json() or {}
    except ValueError as exc:
        raise SlackIntegrationError(f"Slack {action} returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise SlackIntegrationError(f"Slack {action} returned an invalid response.")
    if not payload.get("ok", False):
        error = str(payload.get("error") or "unknown_error")
        if error == "missing_scope":
            needed = str(payload.get("needed") or "").strip()
            detail = f" Missing scope: {needed}." if needed else ""
            raise SlackIntegrationError(f"Slack {action} requires additional scopes.{detail} Reconnect Slack.")
        if error in {"not_in_channel", "channel_not_found", "is_archived"}:
            raise SlackIntegrationError(f"Slack cannot access that channel ({error}). Reconnect Slack or choose another channel.")
        raise SlackIntegrationError(f"Slack {action} failed: {error}.")
    return payload


def _fetch_slack_channels(agent: PersistentAgent, *, limit: int = 200) -> list[Mapping[str, Any]]:
    channels: list[Mapping[str, Any]] = []
    cursor = ""
    page_limit = max(1, min(int(limit), 200))
    while True:
        params = {
            "exclude_archived": "true",
            "limit": str(page_limit),
            "types": "public_channel,private_channel",
        }
        if cursor:
            params["cursor"] = cursor
        response = requests.get(
            f"{SLACK_API_BASE}/conversations.list",
            params=params,
            headers=_slack_headers(agent),
            timeout=20,
        )
        payload = _raise_for_slack_response(response, action="channel discovery")
        page_channels = payload.get("channels") or []
        if not isinstance(page_channels, list):
            raise SlackIntegrationError("Slack channel discovery returned invalid channel data.")
        channels.extend(channel for channel in page_channels if isinstance(channel, Mapping))
        metadata = payload.get("response_metadata") if isinstance(payload.get("response_metadata"), Mapping) else {}
        cursor = str(metadata.get("next_cursor") or "").strip()
        if not cursor or len(channels) >= limit:
            return channels[:limit]


def serialize_workspace(workspace: PersistentAgentSlackWorkspace) -> dict[str, str]:
    return {
        "workspace_id": str(workspace.id),
        "team_id": workspace.team_id,
        "team_name": workspace.team_name,
        "enterprise_id": workspace.enterprise_id,
        "enterprise_name": workspace.enterprise_name,
        "bot_user_id": workspace.bot_user_id,
    }


def list_claimed_workspaces(agent: PersistentAgent) -> list[dict[str, str]]:
    return [serialize_workspace(workspace) for workspace in _claimed_workspace_queryset(agent).order_by("team_name", "team_id")]


def _channel_type(channel: Mapping[str, Any]) -> str:
    if channel.get("is_private"):
        return SLACK_PRIVATE_CHANNEL_TYPE
    return SLACK_PUBLIC_CHANNEL_TYPE


def discover_channels(agent: PersistentAgent, *, query: str = "", limit: int = 100) -> dict[str, Any]:
    workspaces = list(_claimed_workspace_queryset(agent).order_by("team_name", "team_id"))
    if not workspaces:
        return slack_setup_required_response(agent)

    query_lc = query.strip().lower()
    try:
        slack_channels = _fetch_slack_channels(agent, limit=max(1, min(limit, 200)))
    except SlackIntegrationError as exc:
        return {
            "status": "action_required",
            "message": str(exc),
            "setup_url": native_integration_setup_url(),
            "channels": [],
        }

    workspace = workspaces[0]
    channels: list[dict[str, str]] = []
    for channel in slack_channels:
        channel_id = str(channel.get("id") or "").strip()
        channel_name = str(channel.get("name") or channel_id).strip()
        channel_type = _channel_type(channel)
        if not channel_id or channel_type not in SLACK_SUPPORTED_CHANNEL_TYPES:
            continue
        label = f"{workspace.team_name} / #{channel_name}"
        if query_lc and query_lc not in label.lower() and query_lc not in channel_id:
            continue
        channels.append(
            {
                "workspace_id": str(workspace.id),
                "team_id": workspace.team_id,
                "team_name": workspace.team_name,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "channel_type": channel_type,
                "label": label,
            }
        )
    return {"status": "success", "channels": channels}


def serialize_subscription(subscription: PersistentAgentSlackChannelSubscription) -> dict[str, str]:
    return {
        "id": str(subscription.id),
        "agent_id": str(subscription.agent_id),
        "workspace_id": str(subscription.workspace_id),
        "team_id": subscription.workspace.team_id,
        "team_name": subscription.workspace.team_name,
        "channel_id": subscription.channel_id,
        "channel_name": subscription.channel_name,
        "channel_type": subscription.channel_type,
        "status": subscription.status,
        "last_message_at": subscription.last_message_at.isoformat() if subscription.last_message_at else "",
    }


def list_subscriptions(agent: PersistentAgent) -> list[dict[str, str]]:
    subscriptions = (
        PersistentAgentSlackChannelSubscription.objects.select_related("workspace")
        .filter(agent=agent)
        .order_by("workspace__team_name", "channel_name", "channel_id")
    )
    return [serialize_subscription(subscription) for subscription in subscriptions]


def ensure_subscription(
    agent: PersistentAgent,
    *,
    workspace_id: str,
    channel_id: str,
    channel_name: str = "",
    channel_type: str = "",
) -> dict[str, Any]:
    channel_id = channel_id.strip()
    workspace = _claimed_workspace_queryset(agent).get(id=workspace_id, is_active=True)
    canonical_channel_name = (channel_name or channel_id).strip()
    canonical_channel_type = channel_type.strip() or SLACK_PUBLIC_CHANNEL_TYPE
    if canonical_channel_type not in SLACK_SUPPORTED_CHANNEL_TYPES:
        raise SlackIntegrationError("Slack channel must be a public or private channel.")

    with transaction.atomic():
        workspace = _claimed_workspace_queryset(agent).select_for_update().get(id=workspace_id, is_active=True)
        existing = (
            PersistentAgentSlackChannelSubscription.objects.select_for_update()
            .filter(
                agent=agent,
                workspace=workspace,
                channel_id=channel_id,
                status=PersistentAgentSlackChannelSubscription.Status.ACTIVE,
            )
            .first()
        )
        if existing:
            updates = []
            if canonical_channel_name and existing.channel_name != canonical_channel_name:
                existing.channel_name = canonical_channel_name
                updates.append("channel_name")
            if canonical_channel_type and existing.channel_type != canonical_channel_type:
                existing.channel_type = canonical_channel_type
                updates.append("channel_type")
            if updates:
                updates.append("updated_at")
                existing.save(update_fields=updates)
            return {"subscription": serialize_subscription(existing), "created": False, "reused": True}

        try:
            subscription = PersistentAgentSlackChannelSubscription.objects.create(
                agent=agent,
                workspace=workspace,
                channel_id=channel_id,
                channel_name=canonical_channel_name,
                channel_type=canonical_channel_type,
            )
        except IntegrityError as exc:
            raise SlackIntegrationError("This agent is already subscribed to that Slack channel.") from exc
        return {"subscription": serialize_subscription(subscription), "created": True, "reused": False}


def disable_subscription(agent: PersistentAgent, subscription_id: str) -> dict[str, str]:
    subscription = PersistentAgentSlackChannelSubscription.objects.select_related("workspace").get(
        id=subscription_id,
        agent=agent,
    )
    subscription.status = PersistentAgentSlackChannelSubscription.Status.DISABLED
    subscription.save(update_fields=["status", "updated_at"])
    return serialize_subscription(subscription)


def _agent_avatar_url(agent: PersistentAgent) -> str:
    return build_public_agent_avatar_thumbnail_url(agent) or ""


def send_channel_message(agent: PersistentAgent, *, channel_id: str, body: str) -> PersistentAgentMessage:
    body = decode_unicode_character_escapes(body).strip()
    if not body:
        raise ValueError("message is required.")

    subscription = (
        PersistentAgentSlackChannelSubscription.objects.select_related("workspace")
        .get(agent=agent, channel_id=channel_id, status=PersistentAgentSlackChannelSubscription.Status.ACTIVE)
    )
    payload: dict[str, Any] = {
        "channel": subscription.channel_id,
        "text": body,
        "username": (agent.name or "").strip() or "Agent",
    }
    avatar_url = _agent_avatar_url(agent)
    if avatar_url:
        payload["icon_url"] = avatar_url

    response = requests.post(
        f"{SLACK_API_BASE}/chat.postMessage",
        json=payload,
        headers=_slack_headers(agent),
        timeout=20,
    )
    response_payload = _raise_for_slack_response(response, action="message send")
    slack_ts = str(response_payload.get("ts") or "")
    raw_payload = {
        "source": "slack_bot_api",
        "source_kind": "slack",
        "slack_message_ts": slack_ts,
        "slack_channel_id": subscription.channel_id,
        "slack_channel_name": subscription.channel_name,
        "slack_team_id": subscription.workspace.team_id,
        "slack_team_name": subscription.workspace.team_name,
        "slack_platform_channel_address": slack_channel_address(subscription.workspace.team_id, subscription.channel_id),
        "slack_conversation_address": slack_conversation_address(agent.id, subscription.workspace.team_id, subscription.channel_id),
        "source_label": slack_channel_source_label(subscription.channel_id, subscription.channel_name),
        "slack_response": response_payload,
    }
    return create_slack_outbound_message(
        agent,
        channel_id=subscription.channel_id,
        body=body,
        conversation_address=slack_conversation_address(agent.id, subscription.workspace.team_id, subscription.channel_id),
        platform_channel_address=slack_channel_address(subscription.workspace.team_id, subscription.channel_id),
        channel_name=subscription.channel_name,
        raw_payload=raw_payload,
    )


def verify_slack_signature(*, body: bytes, timestamp: str, signature: str) -> bool:
    if not settings.SLACK_SIGNING_SECRET:
        return False
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - timestamp_int) > SLACK_SIGNATURE_MAX_AGE_SECONDS:
        return False
    base = b"v0:" + str(timestamp).encode("utf-8") + b":" + body
    digest = hmac.new(settings.SLACK_SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature or "")


def _ingest_event_for_subscription(
    message: SlackEventMessage,
    subscription: PersistentAgentSlackChannelSubscription,
) -> dict[str, Any]:
    agent = subscription.agent
    platform_channel_address = slack_channel_address(message.team_id, message.channel_id)
    conversation_address = slack_conversation_address(agent.id, message.team_id, message.channel_id)
    source_label = slack_channel_source_label(message.channel_id, message.channel_name)
    raw_payload = {
        "source": "slack_events_api",
        "source_kind": "slack",
        "subscription_id": str(subscription.id),
        "slack_event_id": message.event_id,
        "slack_message_ts": message.ts,
        "slack_thread_ts": message.thread_ts,
        "slack_content": message.text,
        "slack_channel_id": message.channel_id,
        "slack_channel_name": message.channel_name,
        "slack_channel_type": message.channel_type,
        "slack_team_id": message.team_id,
        "slack_author_id": message.user_id,
        "slack_platform_channel_address": platform_channel_address,
        "slack_conversation_address": conversation_address,
        "source_label": source_label,
        "slack_event": dict(message.raw_event),
    }
    parsed = ParsedMessage(
        sender=platform_channel_address,
        recipient=slack_agent_address(agent.id),
        subject=None,
        body=message.text,
        attachments=[],
        raw_payload=raw_payload,
        msg_channel=CommsChannel.SLACK.value,
        conversation_address=conversation_address,
    )
    ensure_slack_agent_endpoint(agent)
    info = ingest_inbound_message(
        CommsChannel.SLACK,
        parsed,
        filespace_import_mode="sync",
        trigger_processing=False,
    )
    display_name = slack_channel_source_label(message.channel_id, message.channel_name)
    if info.message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=info.message.conversation_id).update(display_name=display_name)
    debounce_result = schedule_slack_inbound_processing(str(agent.id))
    subscription.record_message()
    return {
        "agent_id": str(agent.id),
        "subscription_id": str(subscription.id),
        "message_id": str(info.message.id),
        "conversation_id": str(info.message.conversation_id) if info.message.conversation_id else "",
        "debounced": bool(debounce_result.get("debounced")),
        "debounce_seconds": debounce_result.get("debounce_seconds", 0),
    }


def ingest_event_message(message: SlackEventMessage) -> dict[str, Any]:
    try:
        with transaction.atomic():
            _receipt, created = PersistentAgentSlackEventReceipt.objects.get_or_create(
                event_id=message.event_id,
                defaults={
                    "team_id": message.team_id,
                    "event_type": "message",
                    "channel_id": message.channel_id,
                },
            )
        if not created:
            return {"ignored": True, "reason": "duplicate_event"}
    except IntegrityError:
        return {"ignored": True, "reason": "duplicate_event"}

    subscriptions = list(
        PersistentAgentSlackChannelSubscription.objects.select_related("agent", "workspace")
        .filter(
            workspace__team_id=message.team_id,
            channel_id=message.channel_id,
            status=PersistentAgentSlackChannelSubscription.Status.ACTIVE,
            workspace__is_active=True,
        )
    )
    if not subscriptions:
        return {"ignored": True, "reason": "no_subscriptions", "subscription_count": 0}

    deliveries = [_ingest_event_for_subscription(message, subscription) for subscription in subscriptions]
    first_delivery = deliveries[0]
    return {
        "ignored": False,
        "message_id": first_delivery["message_id"],
        "conversation_id": first_delivery["conversation_id"],
        "debounced": first_delivery["debounced"],
        "debounce_seconds": first_delivery["debounce_seconds"],
        "subscription_count": len(deliveries),
        "deliveries": deliveries,
    }


def slack_event_message_from_payload(payload: Mapping[str, Any]) -> SlackEventMessage | None:
    event = payload.get("event") if isinstance(payload.get("event"), Mapping) else {}
    if event.get("type") != "message":
        return None
    if event.get("subtype") or event.get("bot_id") or event.get("app_id"):
        return None
    text = str(event.get("text") or "").strip()
    if not text:
        return None
    user_id = str(event.get("user") or "").strip()
    if not user_id:
        return None
    channel_id = str(event.get("channel") or "").strip()
    team_id = str(event.get("team") or payload.get("team_id") or "").strip()
    event_id = str(payload.get("event_id") or "").strip()
    if not channel_id or not team_id or not event_id:
        return None
    channel_type = str(event.get("channel_type") or "").strip()
    if channel_type in {"im", "mpim"}:
        return None
    channel_name = str(event.get("channel_name") or "").strip()
    return SlackEventMessage(
        event_id=event_id,
        team_id=team_id,
        channel_id=channel_id,
        channel_name=channel_name,
        channel_type=channel_type,
        user_id=user_id,
        text=text,
        ts=str(event.get("ts") or ""),
        thread_ts=str(event.get("thread_ts") or ""),
        raw_event=event,
    )
