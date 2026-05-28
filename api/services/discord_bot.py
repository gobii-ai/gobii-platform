"""Native Gobii Discord bot integration."""

import json
import hashlib
import logging
import secrets
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentDiscordChannelSubscription,
    PersistentAgentDiscordGuild,
    PersistentAgentDiscordOAuthSession,
    PersistentAgentDiscordWebhook,
    PersistentAgentDiscordWebhookEcho,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)
from api.agent.files.attachment_helpers import ResolvedAttachment, create_message_attachments
from api.agent.files.filespace_service import broadcast_message_attachment_update
from api.services.agent_avatar_public import build_public_agent_avatar_thumbnail_url
from api.services.discord_messages import (
    create_discord_outbound_message,
    discord_agent_address,
    discord_channel_address,
    discord_channel_source_label,
    discord_conversation_address,
    ensure_discord_agent_endpoint,
    schedule_discord_inbound_processing,
)
from util.text_sanitizer import decode_unicode_escapes

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_OAUTH_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_MANAGE_GUILD_PERMISSION = 0x20
DISCORD_ADMINISTRATOR_PERMISSION = 0x8
DISCORD_TEXT_CHANNEL_TYPES = {0, 5}
DISCORD_WEBHOOK_MAX_FILES = 10
DISCORD_OAUTH_USER_SCOPES = ("identify", "guilds")
DISCORD_OAUTH_BOT_INSTALL_SCOPES = ("bot", "applications.commands")


class DiscordBotIntegrationError(RuntimeError):
    """Raised when native Discord bot setup or delivery cannot continue."""


@dataclass(frozen=True)
class DiscordGuildClaimResult:
    claimed_count: int
    guilds: list[dict[str, str]]
    selected_guild_id: str = ""
    selected_guild: dict[str, str] | None = None


@dataclass(frozen=True)
class DiscordGatewayMessage:
    message_id: str
    channel_id: str
    channel_name: str
    guild_id: str
    guild_name: str
    author_id: str
    author_name: str
    content: str
    attachments: list[dict[str, Any]]
    embeds: list[dict[str, Any]]
    author_is_bot: bool = False
    webhook_id: str = ""


def _public_base_url() -> str:
    return settings.PUBLIC_SITE_URL.strip().rstrip("/")


def _agent_owner(agent: PersistentAgent) -> tuple[Any, Any]:
    if agent.organization_id:
        return None, agent.organization
    return agent.user, None


def _claimed_guild_queryset(agent: PersistentAgent):
    owner_user, organization = _agent_owner(agent)
    queryset = PersistentAgentDiscordGuild.objects.filter(is_active=True)
    if organization is not None:
        return queryset.filter(organization=organization)
    return queryset.filter(owner_user=owner_user)


def _discord_bot_headers() -> dict[str, str]:
    if not settings.DISCORD_BOT_TOKEN:
        raise DiscordBotIntegrationError("DISCORD_BOT_TOKEN is not configured.")
    return {
        "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def _raise_for_discord_status(response: requests.Response, *, action: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = (response.text or "")[:1000]
        message = f"Discord {action} failed with HTTP {response.status_code}."
        if response_text:
            message = f"{message} Response: {response_text}"
        raise DiscordBotIntegrationError(message) from exc


def _oauth_redirect_uri() -> str:
    return settings.DISCORD_OAUTH_REDIRECT_URI.strip()


def build_discord_oauth_start_url(agent: PersistentAgent) -> str:
    path = reverse("discord_oauth_start")
    return f"{_public_base_url()}{path}?{urlencode({'agent_id': str(agent.id)})}"


def build_discord_bot_invite_url() -> str:
    if not settings.DISCORD_CLIENT_ID:
        return ""
    params = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "scope": " ".join(DISCORD_OAUTH_BOT_INSTALL_SCOPES),
        "permissions": str(settings.DISCORD_BOT_INVITE_PERMISSIONS),
    }
    return f"{DISCORD_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def discord_setup_required_response(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "status": "action_required",
        "message": (
            "Connect Discord to Gobii. This single setup link authorizes Discord guild access "
            "and installs the Gobii bot in the selected server."
        ),
        "connect_url": build_discord_oauth_start_url(agent),
        "bot_invite_url": build_discord_bot_invite_url(),
        "channels": [],
    }


def start_discord_oauth(agent: PersistentAgent, initiated_by) -> str:
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise DiscordBotIntegrationError("Discord OAuth is not configured.")

    owner_user, organization = _agent_owner(agent)
    session = PersistentAgentDiscordOAuthSession.objects.create(
        state=secrets.token_urlsafe(32),
        agent=agent,
        owner_user=owner_user,
        organization=organization,
        initiated_by=initiated_by if getattr(initiated_by, "is_authenticated", False) else None,
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    params = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(),
        "response_type": "code",
        "scope": " ".join((*DISCORD_OAUTH_USER_SCOPES, *DISCORD_OAUTH_BOT_INSTALL_SCOPES)),
        "permissions": str(settings.DISCORD_BOT_INVITE_PERMISSIONS),
        "state": session.state,
        "prompt": "consent",
    }
    return f"{DISCORD_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def _exchange_oauth_code(code: str) -> str:
    response = requests.post(
        DISCORD_OAUTH_TOKEN_URL,
        data={
            "client_id": settings.DISCORD_CLIENT_ID,
            "client_secret": settings.DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _oauth_redirect_uri(),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    _raise_for_discord_status(response, action="OAuth token exchange")
    access_token = str((response.json() or {}).get("access_token") or "").strip()
    if not access_token:
        raise DiscordBotIntegrationError("Discord OAuth did not return an access token.")
    return access_token


def _fetch_oauth_guilds(access_token: str) -> list[Mapping[str, Any]]:
    response = requests.get(
        f"{DISCORD_API_BASE}/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    _raise_for_discord_status(response, action="guild lookup")
    payload = response.json() or []
    if not isinstance(payload, list):
        raise DiscordBotIntegrationError("Discord guild lookup returned an invalid response.")
    return [guild for guild in payload if isinstance(guild, Mapping)]


def _can_manage_guild(guild: Mapping[str, Any]) -> bool:
    try:
        permissions = int(str(guild.get("permissions") or "0"))
    except ValueError:
        return False
    return bool(permissions & DISCORD_ADMINISTRATOR_PERMISSION or permissions & DISCORD_MANAGE_GUILD_PERMISSION)


def _owner_matches_discord_guild_claim(
    guild_claim: PersistentAgentDiscordGuild,
    session: PersistentAgentDiscordOAuthSession,
) -> bool:
    return (
        guild_claim.owner_user_id == session.owner_user_id
        and guild_claim.organization_id == session.organization_id
    )


def _update_discord_guild_claim(
    guild_claim: PersistentAgentDiscordGuild,
    defaults: Mapping[str, Any],
) -> PersistentAgentDiscordGuild:
    updates = []
    for field, value in defaults.items():
        if getattr(guild_claim, field) != value:
            setattr(guild_claim, field, value)
            updates.append(field)
    if updates:
        updates.append("updated_at")
        guild_claim.save(update_fields=updates)
    return guild_claim


def _claim_discord_guild_for_session(
    session: PersistentAgentDiscordOAuthSession,
    *,
    guild_id: str,
    defaults: Mapping[str, Any],
) -> PersistentAgentDiscordGuild | None:
    existing = (
        PersistentAgentDiscordGuild.objects.select_for_update()
        .filter(guild_id=guild_id, is_active=True)
        .first()
    )
    if existing:
        if not _owner_matches_discord_guild_claim(existing, session):
            return None
        return _update_discord_guild_claim(existing, defaults)

    try:
        with transaction.atomic():
            return PersistentAgentDiscordGuild.objects.create(guild_id=guild_id, **defaults)
    except IntegrityError:
        existing = (
            PersistentAgentDiscordGuild.objects.select_for_update()
            .filter(guild_id=guild_id, is_active=True)
            .first()
        )
        if not existing or not _owner_matches_discord_guild_claim(existing, session):
            return None
        return _update_discord_guild_claim(existing, defaults)


def _queue_discord_oauth_completion_processing(
    session: PersistentAgentDiscordOAuthSession,
    result: DiscordGuildClaimResult,
) -> None:
    selected_name = ""
    if result.selected_guild:
        selected_name = str(result.selected_guild.get("name") or "")
    selected_fragment = (
        f" Selected server: {selected_name} ({result.selected_guild_id})."
        if result.selected_guild_id
        else ""
    )
    step = PersistentAgentStep.objects.create(
        agent=session.agent,
        description=(
            "Discord connection completed through the native Gobii Discord bot."
            f"{selected_fragment} "
            "Continue setup now: call discord_channel_subscriptions with action=\"discover_channels\". "
            "If selected_guild is returned, use that server and do not ask the user to choose the server again."
        ),
    )
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
        notes=json.dumps(
            {
                "source": "discord_oauth",
                "claimed_count": result.claimed_count,
                "selected_guild_id": result.selected_guild_id,
                "selected_guild": result.selected_guild,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )

    def _trigger_processing() -> None:
        from api.agent.tasks.process_events import process_agent_events_task

        process_agent_events_task.delay(str(session.agent_id))

    transaction.on_commit(_trigger_processing)


def handle_discord_oauth_callback(
    *,
    state: str,
    code: str,
    selected_guild_id: str = "",
    selected_permissions: str = "",
) -> DiscordGuildClaimResult:
    session = PersistentAgentDiscordOAuthSession.objects.get(state=state)
    if session.completed_at:
        raise DiscordBotIntegrationError("This Discord authorization has already been used.")
    if session.is_expired():
        raise DiscordBotIntegrationError("This Discord authorization has expired. Start the connection again.")

    access_token = _exchange_oauth_code(code)
    oauth_guilds = _fetch_oauth_guilds(access_token)
    claimable_guilds = [guild for guild in oauth_guilds if _can_manage_guild(guild)]
    claimed: list[dict[str, str]] = []
    selected_guild_id = selected_guild_id.strip()
    selected_permissions = selected_permissions.strip()
    selected_guild: dict[str, str] | None = None

    with transaction.atomic():
        session = (
            PersistentAgentDiscordOAuthSession.objects.select_for_update()
            .get(state=state)
        )
        if session.completed_at:
            raise DiscordBotIntegrationError("This Discord authorization has already been used.")
        if session.is_expired():
            raise DiscordBotIntegrationError("This Discord authorization has expired. Start the connection again.")

        for guild in claimable_guilds:
            guild_id = str(guild.get("id") or "").strip()
            if not guild_id:
                continue
            defaults = {
                "name": str(guild.get("name") or guild_id)[:255],
                "icon_hash": str(guild.get("icon") or "")[:128],
                "owner_user": session.owner_user,
                "organization": session.organization,
                "claimed_by": session.initiated_by,
                "is_active": True,
                "last_synced_at": timezone.now(),
            }
            guild_claim = _claim_discord_guild_for_session(
                session,
                guild_id=guild_id,
                defaults=defaults,
            )
            if guild_claim is None:
                continue
            claimed.append(
                {
                    "id": guild_claim.guild_id,
                    "name": guild_claim.name,
                    "icon_hash": guild_claim.icon_hash,
                }
            )
            if selected_guild_id and guild_claim.guild_id == selected_guild_id:
                selected_guild = claimed[-1]

        session.completed_at = timezone.now()
        session.selected_guild_id = selected_guild_id if selected_guild else ""
        session.selected_permissions = selected_permissions[:64]
        session.save(update_fields=["completed_at", "selected_guild_id", "selected_permissions"])
        result = DiscordGuildClaimResult(
            claimed_count=len(claimed),
            guilds=claimed,
            selected_guild_id=session.selected_guild_id,
            selected_guild=selected_guild,
        )
        _queue_discord_oauth_completion_processing(session, result)
    return result


def serialize_guild(guild: PersistentAgentDiscordGuild) -> dict[str, str]:
    return {
        "guild_id": guild.guild_id,
        "name": guild.name,
        "icon_hash": guild.icon_hash,
    }


def list_claimed_guilds(agent: PersistentAgent) -> list[dict[str, str]]:
    return [serialize_guild(guild) for guild in _claimed_guild_queryset(agent).order_by("name", "guild_id")]


def latest_selected_guild(agent: PersistentAgent) -> PersistentAgentDiscordGuild | None:
    session = (
        PersistentAgentDiscordOAuthSession.objects.filter(
            agent=agent,
            completed_at__isnull=False,
        )
        .exclude(selected_guild_id="")
        .order_by("-completed_at", "-created_at")
        .first()
    )
    if not session:
        return None
    return _claimed_guild_queryset(agent).filter(guild_id=session.selected_guild_id).first()


def _fetch_bot_channels(guild_id: str) -> list[Mapping[str, Any]]:
    response = requests.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/channels",
        headers=_discord_bot_headers(),
        timeout=20,
    )
    _raise_for_discord_status(response, action="channel lookup")
    payload = response.json() or []
    if not isinstance(payload, list):
        raise DiscordBotIntegrationError("Discord channel lookup returned an invalid response.")
    return [channel for channel in payload if isinstance(channel, Mapping)]


def _validate_text_channel_in_guild(*, guild_id: str, channel_id: str) -> Mapping[str, Any]:
    normalized_channel_id = channel_id.strip()
    for channel in _fetch_bot_channels(guild_id):
        if str(channel.get("id") or "").strip() != normalized_channel_id:
            continue
        if channel.get("type") not in DISCORD_TEXT_CHANNEL_TYPES:
            raise DiscordBotIntegrationError("Discord channel is not a text channel the Gobii bot can use.")
        return channel
    raise DiscordBotIntegrationError("Discord channel was not found in the selected server.")


def _validate_subscription_channel(subscription: PersistentAgentDiscordChannelSubscription) -> Mapping[str, Any]:
    return _validate_text_channel_in_guild(
        guild_id=subscription.guild.guild_id,
        channel_id=subscription.channel_id,
    )


def discover_channels(agent: PersistentAgent, *, guild_id: str = "", query: str = "", limit: int = 100) -> dict[str, Any]:
    claimed = list(_claimed_guild_queryset(agent).order_by("name", "guild_id"))
    if not claimed:
        return discord_setup_required_response(agent)

    query_lc = query.strip().lower()
    requested_guild_id = guild_id.strip()
    selected_guild = latest_selected_guild(agent) if not requested_guild_id else None
    if selected_guild:
        requested_guild_id = selected_guild.guild_id
    channels: list[dict[str, str]] = []
    for guild in claimed:
        if requested_guild_id and guild.guild_id != requested_guild_id:
            continue
        try:
            bot_channels = _fetch_bot_channels(guild.guild_id)
        except DiscordBotIntegrationError as exc:
            return {
                "status": "action_required",
                "message": (
                    f"The Gobii Discord bot cannot list channels for {guild.name}. "
                    "Invite the bot to that server, then try channel discovery again."
                ),
                "bot_invite_url": build_discord_bot_invite_url(),
                "error": str(exc),
                "channels": [],
            }
        for channel in bot_channels:
            channel_type = channel.get("type")
            if channel_type not in DISCORD_TEXT_CHANNEL_TYPES:
                continue
            channel_id = str(channel.get("id") or "").strip()
            channel_name = str(channel.get("name") or channel_id).strip()
            label = f"{guild.name} / #{channel_name}"
            if query_lc and query_lc not in label.lower() and query_lc not in channel_id:
                continue
            channels.append(
                {
                    "guild_id": guild.guild_id,
                    "guild_name": guild.name,
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "label": label,
                }
            )
            if len(channels) >= max(1, min(limit, 200)):
                break
        if len(channels) >= max(1, min(limit, 200)):
            break

    result: dict[str, Any] = {"status": "success", "channels": channels}
    if selected_guild:
        result["selected_guild"] = serialize_guild(selected_guild)
    return result


def serialize_subscription(subscription: PersistentAgentDiscordChannelSubscription) -> dict[str, str]:
    return {
        "id": str(subscription.id),
        "agent_id": str(subscription.agent_id),
        "guild_id": subscription.guild.guild_id,
        "guild_name": subscription.guild.name,
        "channel_id": subscription.channel_id,
        "channel_name": subscription.channel_name,
        "status": subscription.status,
        "last_message_at": subscription.last_message_at.isoformat() if subscription.last_message_at else "",
    }


def list_subscriptions(agent: PersistentAgent) -> list[dict[str, str]]:
    subscriptions = (
        PersistentAgentDiscordChannelSubscription.objects.select_related("guild")
        .filter(agent=agent)
        .order_by("guild__name", "channel_name", "channel_id")
    )
    return [serialize_subscription(subscription) for subscription in subscriptions]


def ensure_subscription(
    agent: PersistentAgent,
    *,
    guild_id: str,
    channel_id: str,
    channel_name: str = "",
) -> dict[str, Any]:
    channel_id = channel_id.strip()
    guild = _claimed_guild_queryset(agent).get(guild_id=guild_id, is_active=True)
    discord_channel = _validate_text_channel_in_guild(guild_id=guild.guild_id, channel_id=channel_id)
    canonical_channel_name = str(discord_channel.get("name") or channel_name or channel_id).strip()

    with transaction.atomic():
        guild = _claimed_guild_queryset(agent).select_for_update().get(guild_id=guild_id, is_active=True)
        existing = (
            PersistentAgentDiscordChannelSubscription.objects.select_for_update()
            .filter(
                agent=agent,
                guild=guild,
                channel_id=channel_id,
                status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
            )
            .first()
        )
        if existing:
            updates = []
            if canonical_channel_name and existing.channel_name != canonical_channel_name:
                existing.channel_name = canonical_channel_name
                updates.append("channel_name")
            if updates:
                updates.append("updated_at")
                existing.save(update_fields=updates)
            return {"subscription": serialize_subscription(existing), "created": False, "reused": True}

        try:
            subscription = PersistentAgentDiscordChannelSubscription.objects.create(
                agent=agent,
                guild=guild,
                channel_id=channel_id,
                channel_name=canonical_channel_name,
            )
        except IntegrityError as exc:
            raise DiscordBotIntegrationError("This agent is already subscribed to that Discord channel.") from exc
        return {"subscription": serialize_subscription(subscription), "created": True, "reused": False}


def disable_subscription(agent: PersistentAgent, subscription_id: str) -> dict[str, str]:
    subscription = PersistentAgentDiscordChannelSubscription.objects.select_related("guild").get(
        id=subscription_id,
        agent=agent,
    )
    subscription.status = PersistentAgentDiscordChannelSubscription.Status.DISABLED
    subscription.save(update_fields=["status", "updated_at"])
    return serialize_subscription(subscription)


def _attachment_downloads(attachments: list[dict[str, Any]]) -> list[dict[str, str]]:
    downloads: list[dict[str, str]] = []
    for attachment in attachments:
        url = str(attachment.get("url") or "").strip()
        if not url:
            continue
        item = {"url": url}
        filename = str(attachment.get("filename") or "").strip()
        if filename:
            item["filename"] = filename
        content_type = str(attachment.get("content_type") or "").strip()
        if content_type:
            item["content_type"] = content_type
        downloads.append(item)
    return downloads


def _ingest_gateway_message_for_subscription(
    message: DiscordGatewayMessage,
    subscription: PersistentAgentDiscordChannelSubscription,
) -> dict[str, Any]:
    agent = subscription.agent
    platform_channel_address = discord_channel_address(message.guild_id, message.channel_id)
    conversation_address = discord_conversation_address(agent.id, message.guild_id, message.channel_id)
    source_label_parts = []
    if message.author_name:
        source_label_parts.append(message.author_name)
    if message.channel_name:
        source_label_parts.append(f"#{message.channel_name.lstrip('#')}")
    source_label = " in ".join(source_label_parts) if source_label_parts else discord_channel_source_label(
        message.channel_id,
        message.channel_name,
    )
    raw_payload = {
        "source": "discord_bot",
        "source_kind": "discord",
        "subscription_id": str(subscription.id),
        "discord_message_id": message.message_id,
        "discord_channel_id": message.channel_id,
        "discord_channel_name": message.channel_name,
        "discord_guild_id": message.guild_id,
        "discord_guild_name": message.guild_name,
        "discord_author_id": message.author_id,
        "discord_author_name": message.author_name,
        "discord_webhook_id": message.webhook_id,
        "discord_attachments": message.attachments,
        "discord_embeds": message.embeds,
        "discord_platform_channel_address": platform_channel_address,
        "discord_conversation_address": conversation_address,
        "source_label": source_label,
    }
    parsed = ParsedMessage(
        sender=platform_channel_address,
        recipient=discord_agent_address(agent.id),
        subject=None,
        body=message.content,
        attachments=_attachment_downloads(message.attachments),
        raw_payload=raw_payload,
        msg_channel=CommsChannel.DISCORD.value,
        conversation_address=conversation_address,
    )
    ensure_discord_agent_endpoint(agent)
    info = ingest_inbound_message(
        CommsChannel.DISCORD,
        parsed,
        filespace_import_mode="sync",
        trigger_processing=False,
    )
    display_name = f"#{message.channel_name.lstrip('#')}" if message.channel_name else f"Discord {message.channel_id}"
    if info.message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=info.message.conversation_id).update(display_name=display_name)
    debounce_result = schedule_discord_inbound_processing(str(agent.id), typing_channel_id=message.channel_id)
    subscription.record_message()
    return {
        "agent_id": str(agent.id),
        "subscription_id": str(subscription.id),
        "message_id": str(info.message.id),
        "conversation_id": str(info.message.conversation_id) if info.message.conversation_id else "",
        "debounced": bool(debounce_result.get("debounced")),
        "debounce_seconds": debounce_result.get("debounce_seconds", 0),
    }


def _webhook_attachment_filenames(attachments: Iterable[Mapping[str, Any]]) -> list[str]:
    filenames = []
    for attachment in attachments:
        filename = str(attachment.get("filename") or "").strip()
        if filename:
            filenames.append(filename)
    return filenames


def _webhook_echo_signature(
    *,
    webhook_id: str,
    channel_id: str,
    username: str,
    body: str,
    attachment_filenames: Iterable[str],
) -> str:
    payload = {
        "webhook_id": webhook_id,
        "channel_id": channel_id,
        "username": username.strip(),
        "body": body,
        "attachment_filenames": sorted(filename.strip() for filename in attachment_filenames if filename.strip()),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _gateway_webhook_echo_signature(message: DiscordGatewayMessage) -> str:
    return _webhook_echo_signature(
        webhook_id=message.webhook_id,
        channel_id=message.channel_id,
        username=message.author_name,
        body=message.content,
        attachment_filenames=_webhook_attachment_filenames(message.attachments),
    )


def _outbound_webhook_echo_signature(
    *,
    webhook: PersistentAgentDiscordWebhook,
    subscription: PersistentAgentDiscordChannelSubscription,
    username: str,
    body: str,
    attachments: Iterable[ResolvedAttachment],
) -> str:
    return _webhook_echo_signature(
        webhook_id=webhook.webhook_id,
        channel_id=subscription.channel_id,
        username=username,
        body=body,
        attachment_filenames=[attachment.filename for attachment in attachments],
    )


def _create_webhook_echo_marker(
    *,
    agent: PersistentAgent,
    webhook: PersistentAgentDiscordWebhook,
    subscription: PersistentAgentDiscordChannelSubscription,
    signature_hash: str,
) -> PersistentAgentDiscordWebhookEcho:
    PersistentAgentDiscordWebhookEcho.objects.filter(expires_at__lte=timezone.now()).delete()
    return PersistentAgentDiscordWebhookEcho.objects.create(
        agent=agent,
        webhook=webhook,
        channel_id=subscription.channel_id,
        discord_webhook_id=webhook.webhook_id,
        signature_hash=signature_hash,
        expires_at=timezone.now() + timedelta(minutes=10),
    )


def _own_webhook_echo_agent_ids(
    message: DiscordGatewayMessage,
    subscriptions: list[PersistentAgentDiscordChannelSubscription],
) -> set[object]:
    if not message.webhook_id or not message.message_id:
        return set()

    agent_ids = [subscription.agent_id for subscription in subscriptions]
    if not agent_ids:
        return set()

    now = timezone.now()
    marker_ids = []
    own_agent_ids = set()
    markers = (
        PersistentAgentDiscordWebhookEcho.objects
        .filter(
            agent_id__in=agent_ids,
            discord_webhook_id=message.webhook_id,
            channel_id=message.channel_id,
            signature_hash=_gateway_webhook_echo_signature(message),
            expires_at__gt=now,
            matched_at__isnull=True,
        )
        .filter(Q(discord_message_id="") | Q(discord_message_id=message.message_id))
        .values_list("id", "agent_id")
    )
    for marker_id, agent_id in markers:
        marker_ids.append(marker_id)
        own_agent_ids.add(agent_id)
    if marker_ids:
        PersistentAgentDiscordWebhookEcho.objects.filter(id__in=marker_ids).update(matched_at=now)
    return own_agent_ids


def ingest_gateway_message(message: DiscordGatewayMessage) -> dict[str, Any]:
    if message.author_is_bot and not message.webhook_id:
        return {"ignored": True, "reason": "bot"}
    if not message.guild_id or not message.channel_id or not message.message_id:
        return {"ignored": True, "reason": "missing_discord_ids"}
    if not message.content and not message.attachments and not message.embeds:
        return {"ignored": True, "reason": "empty_message"}

    subscriptions = list(
        PersistentAgentDiscordChannelSubscription.objects.select_related("agent", "guild")
        .filter(
            guild__guild_id=message.guild_id,
            channel_id=message.channel_id,
            agent__execution_environment=settings.GOBII_RELEASE_ENV,
            status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
        )
        .order_by("created_at", "id")
    )
    if not subscriptions:
        return {"ignored": True, "reason": "no_subscription"}

    own_echo_agent_ids = _own_webhook_echo_agent_ids(message, subscriptions)
    skipped_subscription_ids = []
    deliveries = []
    for subscription in subscriptions:
        if subscription.agent_id in own_echo_agent_ids:
            skipped_subscription_ids.append(str(subscription.id))
            continue
        deliveries.append(_ingest_gateway_message_for_subscription(message, subscription))
    if not deliveries:
        return {
            "ignored": True,
            "reason": "own_webhook_echo",
            "subscription_count": len(subscriptions),
            "skipped_subscription_ids": skipped_subscription_ids,
        }
    first_delivery = deliveries[0]
    return {
        "ignored": False,
        "message_id": first_delivery["message_id"],
        "conversation_id": first_delivery["conversation_id"],
        "debounced": first_delivery["debounced"],
        "debounce_seconds": first_delivery["debounce_seconds"],
        "subscription_count": len(deliveries),
        "deliveries": deliveries,
        "skipped_subscription_ids": skipped_subscription_ids,
    }


def _agent_avatar_url(agent: PersistentAgent) -> str:
    avatar_url = build_public_agent_avatar_thumbnail_url(agent)
    if not avatar_url:
        return ""
    return avatar_url


def _get_or_create_channel_webhook(subscription: PersistentAgentDiscordChannelSubscription) -> PersistentAgentDiscordWebhook:
    _validate_subscription_channel(subscription)
    webhook = PersistentAgentDiscordWebhook.objects.filter(
        guild=subscription.guild,
        channel_id=subscription.channel_id,
    ).first()
    if webhook and webhook.webhook_token:
        return webhook

    response = requests.post(
        f"{DISCORD_API_BASE}/channels/{subscription.channel_id}/webhooks",
        json={"name": "Gobii"},
        headers=_discord_bot_headers(),
        timeout=20,
    )
    _raise_for_discord_status(response, action="webhook creation")
    payload = response.json() or {}
    webhook_id = str(payload.get("id") or "").strip()
    webhook_token = str(payload.get("token") or "").strip()
    if not webhook_id or not webhook_token:
        raise DiscordBotIntegrationError("Discord webhook creation returned an invalid response.")

    webhook, _created = PersistentAgentDiscordWebhook.objects.update_or_create(
        guild=subscription.guild,
        channel_id=subscription.channel_id,
        defaults={
            "webhook_id": webhook_id,
            "name": str(payload.get("name") or "Gobii")[:255],
        },
    )
    webhook.webhook_token = webhook_token
    webhook.save(update_fields=["webhook_token_encrypted", "updated_at"])
    return webhook


def _discord_multipart_files(
    attachments: list[ResolvedAttachment],
    stack: ExitStack,
) -> list[tuple[str, tuple[str, Any, str]]]:
    files = []
    for index, attachment in enumerate(attachments):
        file_obj = attachment.node.content
        file_obj.open("rb")
        stack.callback(file_obj.close)
        files.append(
            (
                f"files[{index}]",
                (attachment.filename, file_obj, attachment.content_type),
            )
        )
    return files


def send_channel_message(
    agent: PersistentAgent,
    *,
    channel_id: str,
    body: str,
    attachments: Iterable[ResolvedAttachment] | None = None,
) -> PersistentAgentMessage:
    resolved_attachments = list(attachments or [])
    body = decode_unicode_escapes(body)
    if not body and not resolved_attachments:
        raise ValueError("message is required when attachments is empty.")
    if len(resolved_attachments) > DISCORD_WEBHOOK_MAX_FILES:
        raise ValueError(f"Discord supports at most {DISCORD_WEBHOOK_MAX_FILES} attachments per message.")
    total_attachment_bytes = sum(max(0, int(attachment.size_bytes or 0)) for attachment in resolved_attachments)
    if (
        settings.DISCORD_WEBHOOK_MAX_TOTAL_ATTACHMENT_BYTES > 0
        and total_attachment_bytes > settings.DISCORD_WEBHOOK_MAX_TOTAL_ATTACHMENT_BYTES
    ):
        raise ValueError(
            "Discord attachments exceed the configured total upload limit "
            f"({total_attachment_bytes} bytes > {settings.DISCORD_WEBHOOK_MAX_TOTAL_ATTACHMENT_BYTES} bytes)."
        )

    subscription = (
        PersistentAgentDiscordChannelSubscription.objects.select_related("guild")
        .get(agent=agent, channel_id=channel_id, status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE)
    )
    webhook = _get_or_create_channel_webhook(subscription)
    username = (agent.name or "").strip() or "Agent"
    payload: dict[str, Any] = {
        "content": body,
        "username": username,
    }
    avatar_url = _agent_avatar_url(agent)
    if avatar_url:
        payload["avatar_url"] = avatar_url
    webhook_url = f"{DISCORD_API_BASE}/webhooks/{webhook.webhook_id}/{webhook.webhook_token}"
    sent_attachments = [
        {
            "path": attachment.path,
            "filename": attachment.filename,
            "content_type": attachment.content_type,
            "size_bytes": attachment.size_bytes,
        }
        for attachment in resolved_attachments
    ]
    echo_signature = _outbound_webhook_echo_signature(
        webhook=webhook,
        subscription=subscription,
        username=username,
        body=body,
        attachments=resolved_attachments,
    )
    echo_marker = _create_webhook_echo_marker(
        agent=agent,
        webhook=webhook,
        subscription=subscription,
        signature_hash=echo_signature,
    )
    try:
        if resolved_attachments:
            with ExitStack() as stack:
                response = requests.post(
                    webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files=_discord_multipart_files(resolved_attachments, stack),
                    params={"wait": "true"},
                    timeout=60,
                )
        else:
            response = requests.post(
                webhook_url,
                json=payload,
                params={"wait": "true"},
                timeout=20,
            )
        _raise_for_discord_status(response, action="webhook send")
    except (requests.RequestException, DiscordBotIntegrationError):
        echo_marker.delete()
        raise
    response_payload = response.json() or {}
    discord_message_id = str(response_payload.get("id") or "")
    echo_marker.discord_message_id = discord_message_id
    echo_marker.save(update_fields=["discord_message_id"])
    raw_payload = {
        "source": "discord_bot_webhook",
        "source_kind": "discord",
        "discord_message_id": discord_message_id,
        "discord_channel_id": subscription.channel_id,
        "discord_channel_name": subscription.channel_name,
        "discord_guild_id": subscription.guild.guild_id,
        "discord_guild_name": subscription.guild.name,
        "discord_platform_channel_address": discord_channel_address(subscription.guild.guild_id, subscription.channel_id),
        "discord_conversation_address": discord_conversation_address(agent.id, subscription.guild.guild_id, subscription.channel_id),
        "webhook_id": webhook.webhook_id,
        "webhook_echo_marker_id": str(echo_marker.id),
        "webhook_echo_signature": echo_signature,
        "source_label": discord_channel_source_label(subscription.channel_id, subscription.channel_name),
        "discord_sent_attachments": sent_attachments,
        "discord_response": response_payload if isinstance(response_payload, Mapping) else {},
    }
    message = create_discord_outbound_message(
        agent,
        channel_id=subscription.channel_id,
        body=body,
        conversation_address=discord_conversation_address(agent.id, subscription.guild.guild_id, subscription.channel_id),
        platform_channel_address=discord_channel_address(subscription.guild.guild_id, subscription.channel_id),
        channel_name=subscription.channel_name,
        raw_payload=raw_payload,
    )
    if resolved_attachments:
        create_message_attachments(message, resolved_attachments)
        broadcast_message_attachment_update(str(message.id))
    return message
