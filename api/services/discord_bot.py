"""Native Gobii Discord bot integration."""

import json
import hashlib
import logging
import re
import secrets
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.models import (
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentDiscordChannelSubscription,
    PersistentAgentDiscordGuild,
    PersistentAgentDiscordOAuthSession,
    PersistentAgentDiscordWebhook,
    PersistentAgentDiscordWebhookEcho,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemStep,
    UserDiscordIdentity,
)
from api.services.inactive_agent_notifications import (
    INACTIVE_AUTO_REPLY_KIND,
    INACTIVE_BLOCKED_INPUT_KIND,
    inactive_auto_reply_body,
    send_inactive_notice_once,
)
from api.agent.system_skills.defaults import DISCORD_NATIVE_SYSTEM_SKILL_KEY
from api.agent.files.attachment_helpers import ResolvedAttachment, create_message_attachments
from api.agent.files.filespace_service import broadcast_message_attachment_update
from api.services.agent_avatar_public import build_public_agent_avatar_thumbnail_url
from api.services.discord_markdown import normalize_discord_markdown
from api.services.discord_messages import (
    create_discord_outbound_message,
    discord_agent_address,
    discord_channel_address,
    discord_channel_source_label,
    discord_conversation_address,
    ensure_discord_agent_endpoint,
    schedule_discord_inbound_processing,
)
from util.text_sanitizer import decode_unicode_character_escapes

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_OAUTH_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_WEBHOOK_USERNAME_MAX_LENGTH = 80
DISCORD_TEXT_CHANNEL_TYPES = {0, 5}
DISCORD_WEBHOOK_MAX_FILES = 10
DISCORD_OAUTH_BOT_INSTALL_SCOPES = ("bot", "applications.commands")
DISCORD_GUILD_INSTALL_TYPE = 0
DISCORD_IDENTITY_OAUTH_STATE_PREFIX = "identity_"
DISCORD_IDENTITY_OAUTH_STATE_SALT = "gobii.discord.identity_oauth_state"
DISCORD_IDENTITY_OAUTH_STATE_MAX_AGE_SECONDS = 15 * 60


class DiscordBotIntegrationError(RuntimeError):
    """Raised when native Discord bot setup or delivery cannot continue."""


_DISCORD_CUSTOM_EMOJI_PATTERN = re.compile(r"^<a?:([A-Za-z0-9_]+):(\d+)>$")
_DISCORD_CUSTOM_EMOJI_API_PATTERN = re.compile(r"^[A-Za-z0-9_]+:\d+$")


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
    raw_content: str = ""
    author_is_bot: bool = False
    webhook_id: str = ""
    reply_to: dict[str, Any] | None = None


def _public_base_url() -> str:
    return settings.PUBLIC_SITE_URL.strip().rstrip("/")


def _agent_owner(agent: PersistentAgent) -> tuple[Any, Any]:
    if agent.organization_id:
        return None, agent.organization
    return agent.user, None


def _claimed_guild_queryset(agent: PersistentAgent):
    owner_user, organization = _agent_owner(agent)
    return claimed_guild_queryset_for_owner(
        owner_user=owner_user,
        organization=organization,
    )


def claimed_guild_queryset_for_owner(*, owner_user=None, organization=None, include_legacy: bool = False):
    if (owner_user is None) == (organization is None):
        raise ValueError("Exactly one Discord owner must be provided.")

    queryset = PersistentAgentDiscordGuild.objects.filter(is_active=True)
    if not include_legacy:
        supported_subscription = PersistentAgentDiscordChannelSubscription.objects.filter(
            guild_id=OuterRef("pk"),
            status__in=[
                PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
                PersistentAgentDiscordChannelSubscription.Status.ERROR,
            ],
        )
        queryset = queryset.alias(has_supported_subscription=Exists(supported_subscription)).filter(
            Q(authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.EXPLICIT_OAUTH)
            | Q(has_supported_subscription=True)
        )
    if organization is not None:
        return queryset.filter(organization=organization)
    return queryset.filter(owner_user=owner_user)


def _discord_bot_headers() -> dict[str, str]:
    if not settings.DISCORD_BOT_TOKEN:
        raise DiscordBotIntegrationError("DISCORD_BOT_TOKEN is not configured.")
    return {"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"}


def _raise_for_discord_status(response: requests.Response, *, action: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = (response.text or "")[:1000]
        message = f"Discord {action} failed with HTTP {response.status_code}."
        if response_text:
            message = f"{message} Response: {response_text}"
        raise DiscordBotIntegrationError(message) from exc


def normalize_discord_reaction_emoji(emoji: str) -> str:
    normalized = str(emoji or "").strip()
    if not normalized:
        raise ValueError("emoji is required.")

    custom_match = _DISCORD_CUSTOM_EMOJI_PATTERN.fullmatch(normalized)
    if custom_match:
        return f"{custom_match.group(1)}:{custom_match.group(2)}"
    if normalized.startswith("<:") or normalized.startswith("<a:"):
        raise ValueError("Custom Discord emoji must use <:name:id>, <a:name:id>, or name:id format.")
    if ":" in normalized and not _DISCORD_CUSTOM_EMOJI_API_PATTERN.fullmatch(normalized):
        raise ValueError("Custom Discord emoji must use <:name:id>, <a:name:id>, or name:id format.")
    return normalized


def add_discord_reaction(
    agent: PersistentAgent,
    *,
    channel_id: str,
    message_id: str,
    emoji: str,
) -> str:
    normalized_channel_id = str(channel_id or "").strip()
    normalized_message_id = str(message_id or "").strip()
    if not normalized_channel_id:
        raise ValueError("channel_id is required.")
    if not normalized_message_id:
        raise ValueError("message_id is required.")
    normalized_emoji = normalize_discord_reaction_emoji(emoji)

    PersistentAgentDiscordChannelSubscription.objects.get(
        agent=agent,
        channel_id=normalized_channel_id,
        status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
    )
    encoded_emoji = quote(normalized_emoji, safe="")
    reaction_url = (
        f"{DISCORD_API_BASE}/channels/{normalized_channel_id}/messages/"
        f"{normalized_message_id}/reactions/{encoded_emoji}/@me"
    )
    response = requests.put(
        reaction_url,
        headers=_discord_bot_headers(),
        timeout=20,
    )
    if response.status_code == 400:
        raise DiscordBotIntegrationError(
            "Discord rejected that emoji. Use one Unicode emoji or a custom emoji in name:id format."
        )
    if response.status_code == 403:
        raise DiscordBotIntegrationError(
            "Discord denied the reaction. Grant the Gobii bot Add Reactions and Read Message History permissions "
            "for this channel, or reconnect Discord to refresh its permissions."
        )
    if response.status_code == 404:
        raise DiscordBotIntegrationError(
            "Discord could not find that message in the subscribed channel. Check the channel_id and message_id."
        )
    _raise_for_discord_status(response, action="reaction creation")
    return normalized_emoji


def _oauth_redirect_uri() -> str:
    return settings.DISCORD_OAUTH_REDIRECT_URI.strip()


def build_discord_oauth_start_url(agent: PersistentAgent, *, guild_id: str = "") -> str:
    path = reverse("discord_oauth_start")
    params = {"agent_id": str(agent.id)}
    normalized_guild_id = str(guild_id or "").strip()
    if normalized_guild_id:
        params["guild_id"] = normalized_guild_id
    return f"{_public_base_url()}{path}?{urlencode(params)}"


def _discord_oauth_url(session: PersistentAgentDiscordOAuthSession) -> str:
    params = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(DISCORD_OAUTH_BOT_INSTALL_SCOPES),
        "permissions": str(settings.DISCORD_BOT_INVITE_PERMISSIONS),
        "integration_type": str(DISCORD_GUILD_INSTALL_TYPE),
        "state": session.state,
        "prompt": "consent",
    }
    if session.requested_guild_id:
        params["guild_id"] = session.requested_guild_id
        params["disable_guild_select"] = "true"
    return f"{DISCORD_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def _discord_identity_oauth_url(state: str) -> str:
    params = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(),
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    }
    return f"{DISCORD_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def discord_setup_required_response(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "status": "action_required",
        "message": (
            "Connect one Discord server to Gobii. This setup link installs the Gobii bot "
            "only in the server selected in Discord."
        ),
        "connect_url": build_discord_oauth_start_url(agent),
        "channels": [],
    }


def start_discord_oauth(agent: PersistentAgent, initiated_by, *, requested_guild_id: str = "") -> str:
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise DiscordBotIntegrationError("Discord OAuth is not configured.")

    requested_guild_id = str(requested_guild_id or "").strip()
    if requested_guild_id and not _claimed_guild_queryset(agent).filter(guild_id=requested_guild_id).exists():
        raise DiscordBotIntegrationError("That Discord server is not connected to this Gobii context.")

    owner_user, organization = _agent_owner(agent)
    session = PersistentAgentDiscordOAuthSession.objects.create(
        state=secrets.token_urlsafe(32),
        agent=agent,
        owner_user=owner_user,
        organization=organization,
        initiated_by=initiated_by if getattr(initiated_by, "is_authenticated", False) else None,
        requested_guild_id=requested_guild_id,
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    return _discord_oauth_url(session)


def start_discord_identity_oauth(user) -> str:
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise DiscordBotIntegrationError("Discord OAuth is not configured.")

    state = signing.dumps(
        {"user_id": str(user.pk), "nonce": secrets.token_urlsafe(16)},
        salt=DISCORD_IDENTITY_OAUTH_STATE_SALT,
        compress=True,
    )
    return _discord_identity_oauth_url(f"{DISCORD_IDENTITY_OAUTH_STATE_PREFIX}{state}")


def _exchange_oauth_code(code: str, *, required_scope: str = "") -> Mapping[str, Any]:
    try:
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
    except requests.RequestException as exc:
        raise DiscordBotIntegrationError("Discord OAuth token exchange could not reach Discord.") from exc
    _raise_for_discord_status(response, action="OAuth token exchange")
    payload = response.json() or {}
    if not isinstance(payload, Mapping):
        raise DiscordBotIntegrationError("Discord OAuth returned an invalid token response.")
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise DiscordBotIntegrationError("Discord OAuth did not return an access token.")
    if required_scope:
        scopes = {scope.strip() for scope in str(payload.get("scope") or "").split() if scope.strip()}
        if required_scope not in scopes:
            raise DiscordBotIntegrationError(f"Discord OAuth did not grant {required_scope} access.")
    else:
        guild = payload.get("guild")
        if isinstance(guild, Mapping) and str(guild.get("id") or "").strip():
            return payload
        raise DiscordBotIntegrationError(
            "Discord OAuth did not identify the installed server. "
            "Enable Require OAuth2 Code Grant for the Gobii Discord application, then try again."
        )
    return payload


def _validate_discord_identity_oauth_state(state: str, user) -> None:
    if not state.startswith(DISCORD_IDENTITY_OAUTH_STATE_PREFIX):
        raise DiscordBotIntegrationError("Discord identity authorization is invalid. Start verification again.")
    try:
        payload = signing.loads(
            state.removeprefix(DISCORD_IDENTITY_OAUTH_STATE_PREFIX),
            salt=DISCORD_IDENTITY_OAUTH_STATE_SALT,
            max_age=DISCORD_IDENTITY_OAUTH_STATE_MAX_AGE_SECONDS,
        )
    except signing.SignatureExpired as exc:
        raise DiscordBotIntegrationError(
            "This Discord identity authorization has expired. Start verification again."
        ) from exc
    except signing.BadSignature as exc:
        raise DiscordBotIntegrationError(
            "Discord identity authorization is invalid. Start verification again."
        ) from exc
    if not isinstance(payload, Mapping) or payload.get("user_id") != str(user.pk):
        raise DiscordBotIntegrationError("Discord identity authorization was not created for this user.")


def _fetch_discord_current_user(access_token: str) -> Mapping[str, Any]:
    try:
        response = requests.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise DiscordBotIntegrationError("Gobii could not verify the Discord account.") from exc
    _raise_for_discord_status(response, action="account verification")
    payload = response.json() or {}
    discord_user_id = str(payload.get("id") or "").strip() if isinstance(payload, Mapping) else ""
    username = str(payload.get("username") or "").strip() if isinstance(payload, Mapping) else ""
    if not discord_user_id.isdigit() or not username:
        raise DiscordBotIntegrationError("Discord returned an invalid account identity.")
    return payload


def handle_discord_identity_oauth_callback(*, state: str, code: str, user) -> UserDiscordIdentity:
    _validate_discord_identity_oauth_state(state, user)
    token_payload = _exchange_oauth_code(code, required_scope="identify")
    discord_user = _fetch_discord_current_user(str(token_payload["access_token"]))
    discord_user_id = str(discord_user["id"]).strip()
    username = str(discord_user["username"]).strip()[:255]
    global_name = str(discord_user.get("global_name") or "").strip()[:255]

    try:
        identity, _created = UserDiscordIdentity.objects.update_or_create(
            user=user,
            defaults={
                "discord_user_id": discord_user_id,
                "username": username,
                "global_name": global_name,
                "verified_at": timezone.now(),
            },
        )
    except IntegrityError as exc:
        raise DiscordBotIntegrationError("This Discord account is already linked to another Gobii user.") from exc
    return identity


def _fetch_bot_guild(guild_id: str) -> Mapping[str, Any]:
    try:
        response = requests.get(
            f"{DISCORD_API_BASE}/guilds/{guild_id}",
            headers=_discord_bot_headers(),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise DiscordBotIntegrationError(
            "Gobii could not verify the installed Discord server."
        ) from exc
    _raise_for_discord_status(response, action="installed server verification")
    payload = response.json() or {}
    if not isinstance(payload, Mapping) or str(payload.get("id") or "").strip() != guild_id:
        raise DiscordBotIntegrationError("Discord returned an invalid installed server response.")
    return payload


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


def serialize_guild(guild: PersistentAgentDiscordGuild) -> dict[str, str]:
    return {
        "guild_id": guild.guild_id,
        "name": guild.name,
        "icon_hash": guild.icon_hash,
    }


def _queue_discord_oauth_completion_processing(
    session: PersistentAgentDiscordOAuthSession,
    guild: dict[str, str],
) -> None:
    step = PersistentAgentStep.objects.create(
        agent=session.agent,
        description=(
            "Discord connection completed through the native Gobii Discord bot."
            f" Selected server: {guild['name']} ({guild['guild_id']}). "
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
                "claimed_count": 1,
                "selected_guild_id": guild["guild_id"],
                "selected_guild": guild,
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
) -> dict[str, str]:
    session = PersistentAgentDiscordOAuthSession.objects.get(state=state)
    if session.completed_at:
        raise DiscordBotIntegrationError("This Discord authorization has already been used.")
    if session.is_expired():
        raise DiscordBotIntegrationError("This Discord authorization has expired. Start the connection again.")

    token_payload = _exchange_oauth_code(code)
    token_guild = token_payload["guild"]
    authoritative_guild_id = str(token_guild.get("id") or "").strip()
    bot_guild = _fetch_bot_guild(authoritative_guild_id)

    with transaction.atomic():
        session = (
            PersistentAgentDiscordOAuthSession.objects.select_for_update()
            .get(state=state)
        )
        if session.completed_at:
            raise DiscordBotIntegrationError("This Discord authorization has already been used.")
        if session.is_expired():
            raise DiscordBotIntegrationError("This Discord authorization has expired. Start the connection again.")
        if session.requested_guild_id and session.requested_guild_id != authoritative_guild_id:
            raise DiscordBotIntegrationError(
                "Discord authorized a different server than the requested Gobii server."
            )

        defaults = {
            "name": str(bot_guild.get("name") or token_guild.get("name") or authoritative_guild_id)[:255],
            "icon_hash": str(bot_guild.get("icon") or token_guild.get("icon") or "")[:128],
            "authorization_source": PersistentAgentDiscordGuild.AuthorizationSource.EXPLICIT_OAUTH,
            "owner_user": session.owner_user,
            "organization": session.organization,
            "claimed_by": session.initiated_by,
            "is_active": True,
            "last_synced_at": timezone.now(),
        }
        guild_claim = _claim_discord_guild_for_session(
            session,
            guild_id=authoritative_guild_id,
            defaults=defaults,
        )
        if guild_claim is None:
            raise DiscordBotIntegrationError(
                "This Discord server is already connected to another Gobii context."
            )
        session.completed_at = timezone.now()
        session.selected_guild_id = authoritative_guild_id
        session.save(update_fields=["completed_at", "selected_guild_id"])
        guild = serialize_guild(guild_claim)
        _queue_discord_oauth_completion_processing(session, guild)
    return guild


def list_claimed_guilds(agent: PersistentAgent) -> list[dict[str, str]]:
    return [serialize_guild(guild) for guild in _claimed_guild_queryset(agent).order_by("name", "guild_id")]


def list_claimed_guilds_for_owner(*, owner_user=None, organization=None) -> list[dict[str, str]]:
    return [
        serialize_guild(guild)
        for guild in claimed_guild_queryset_for_owner(
            owner_user=owner_user,
            organization=organization,
        ).order_by("name", "guild_id")
    ]


def _delete_discord_resource(url: str, *, action: str, headers: Mapping[str, str] | None = None) -> None:
    try:
        response = requests.delete(url, headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise DiscordBotIntegrationError(f"Discord {action} could not reach Discord.") from exc
    if response.status_code != 404:
        _raise_for_discord_status(response, action=action)


def _delete_discord_webhook(webhook: PersistentAgentDiscordWebhook) -> None:
    try:
        webhook_token = webhook.webhook_token
    except ValueError as exc:
        raise DiscordBotIntegrationError(
            "Gobii could not read the stored Discord webhook while removing the server."
        ) from exc
    if webhook_token:
        _delete_discord_resource(
            f"{DISCORD_API_BASE}/webhooks/{webhook.webhook_id}/{webhook_token}",
            action="webhook removal",
        )
        return
    _delete_discord_resource(
        f"{DISCORD_API_BASE}/webhooks/{webhook.webhook_id}",
        action="webhook removal",
        headers=_discord_bot_headers(),
    )


def _leave_discord_guild(guild_id: str) -> None:
    _delete_discord_resource(
        f"{DISCORD_API_BASE}/users/@me/guilds/{guild_id}",
        action="server removal",
        headers=_discord_bot_headers(),
    )


def disconnect_discord_guild_claim(guild_claim: PersistentAgentDiscordGuild) -> dict[str, int | str]:
    guild_id = guild_claim.guild_id
    webhooks = list(
        PersistentAgentDiscordWebhook.objects.filter(guild=guild_claim).order_by("created_at", "id")
    )
    _leave_discord_guild(guild_id)
    for webhook in webhooks:
        _delete_discord_webhook(webhook)

    now = timezone.now()
    with transaction.atomic():
        locked_claim = PersistentAgentDiscordGuild.objects.select_for_update().get(id=guild_claim.id)
        subscription_count = (
            PersistentAgentDiscordChannelSubscription.objects.filter(guild=locked_claim)
            .exclude(status=PersistentAgentDiscordChannelSubscription.Status.DISABLED)
            .update(
                status=PersistentAgentDiscordChannelSubscription.Status.DISABLED,
                updated_at=now,
            )
        )
        webhook_queryset = PersistentAgentDiscordWebhook.objects.filter(guild=locked_claim)
        webhook_count = webhook_queryset.count()
        webhook_queryset.delete()
        locked_claim.is_active = False
        locked_claim.last_synced_at = now
        locked_claim.save(update_fields=["is_active", "last_synced_at", "updated_at"])

    return {
        "guild_id": guild_id,
        "subscriptions_disabled": subscription_count,
        "webhooks_removed": webhook_count,
    }


def disconnect_discord_guild_for_owner(*, guild_id: str, owner_user=None, organization=None) -> dict[str, int | str]:
    guild_claim = claimed_guild_queryset_for_owner(
        owner_user=owner_user,
        organization=organization,
        include_legacy=True,
    ).get(guild_id=str(guild_id or "").strip())
    return disconnect_discord_guild_claim(guild_claim)


def disconnect_discord_native_integration(*, owner_user=None, organization=None) -> dict[str, Any]:
    if (owner_user is None) == (organization is None):
        raise ValueError("Exactly one Discord owner must be provided.")

    guilds = list(
        claimed_guild_queryset_for_owner(
            owner_user=owner_user,
            organization=organization,
            include_legacy=True,
        ).order_by("name", "guild_id")
    )
    guild_count = 0
    subscription_count = 0
    webhook_count = 0
    failed_guilds = []
    for guild in guilds:
        try:
            result = disconnect_discord_guild_claim(guild)
        except DiscordBotIntegrationError as exc:
            failed_guilds.append(
                {
                    "guild_id": guild.guild_id,
                    "name": guild.name,
                    "error": str(exc),
                }
            )
            continue
        guild_count += 1
        subscription_count += int(result["subscriptions_disabled"])
        webhook_count += int(result["webhooks_removed"])

    agent_queryset = PersistentAgent.objects.non_eval().alive()
    if organization is not None:
        agent_queryset = agent_queryset.filter(organization=organization)
    else:
        agent_queryset = agent_queryset.filter(user=owner_user, organization_id__isnull=True)
    agent_ids = list(agent_queryset.values_list("id", flat=True))
    skill_count = 0
    if not failed_guilds:
        skill_count = PersistentAgentSystemSkillState.objects.filter(
            agent_id__in=agent_ids,
            skill_key=DISCORD_NATIVE_SYSTEM_SKILL_KEY,
            is_enabled=True,
        ).update(is_enabled=False)

    return {
        "guilds_disconnected": guild_count,
        "subscriptions_disabled": subscription_count,
        "webhooks_removed": webhook_count,
        "agents_disabled": skill_count,
        "failed_guilds": failed_guilds,
    }


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


def _normalized_channel_name(value: str) -> str:
    return value.strip().lstrip("#").strip().casefold()


def _resolve_text_channel_in_guild(*, guild_id: str, channel_name: str) -> Mapping[str, Any]:
    normalized_name = _normalized_channel_name(channel_name)
    if not normalized_name:
        raise DiscordBotIntegrationError("channel_id or channel_name is required.")
    matches = [
        channel
        for channel in _fetch_bot_channels(guild_id)
        if channel.get("type") in DISCORD_TEXT_CHANNEL_TYPES
        and _normalized_channel_name(str(channel.get("name") or "")) == normalized_name
    ]
    if not matches:
        raise DiscordBotIntegrationError("Discord channel name was not found in the selected server.")
    if len(matches) > 1:
        raise DiscordBotIntegrationError(
            "Discord channel name matches more than one text channel in the selected server; use channel_id."
        )
    return matches[0]


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
                    "Reconnect that server, then try channel discovery again."
                ),
                "connect_url": build_discord_oauth_start_url(agent, guild_id=guild.guild_id),
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


def resolve_active_subscription(
    agent: PersistentAgent,
    *,
    channel_id: str = "",
    channel_name: str = "",
    guild_id: str = "",
) -> PersistentAgentDiscordChannelSubscription:
    requested_id = channel_id.strip()
    requested_name = _normalized_channel_name(channel_name)
    subscriptions = list(
        PersistentAgentDiscordChannelSubscription.objects.select_related("guild")
        .filter(
            agent=agent,
            status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
        )
        .order_by("guild__name", "channel_name", "channel_id")
    )
    if guild_id.strip():
        subscriptions = [
            subscription for subscription in subscriptions if subscription.guild.guild_id == guild_id.strip()
        ]
    if requested_id:
        subscriptions = [
            subscription for subscription in subscriptions if subscription.channel_id == requested_id
        ]
    if requested_name:
        subscriptions = [
            subscription
            for subscription in subscriptions
            if _normalized_channel_name(subscription.channel_name) == requested_name
        ]
    if not requested_id and not requested_name:
        raise DiscordBotIntegrationError("channel_id or channel_name is required.")
    if not subscriptions:
        raise DiscordBotIntegrationError("No active native Discord subscription matched that channel.")
    if len(subscriptions) > 1:
        raise DiscordBotIntegrationError(
            "That channel name matches more than one subscribed channel; provide guild_id or channel_id."
        )
    return subscriptions[0]


def ensure_subscription(
    agent: PersistentAgent,
    *,
    guild_id: str,
    channel_id: str = "",
    channel_name: str = "",
) -> dict[str, Any]:
    channel_id = channel_id.strip()
    guild = _claimed_guild_queryset(agent).get(guild_id=guild_id, is_active=True)
    if channel_id:
        discord_channel = _validate_text_channel_in_guild(guild_id=guild.guild_id, channel_id=channel_id)
    else:
        discord_channel = _resolve_text_channel_in_guild(guild_id=guild.guild_id, channel_name=channel_name)
        channel_id = str(discord_channel.get("id") or "").strip()
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


def _existing_gateway_message_for_subscription(
    message: DiscordGatewayMessage,
    subscription: PersistentAgentDiscordChannelSubscription,
) -> PersistentAgentMessage | None:
    return (
        PersistentAgentMessage.objects.filter(
            owner_agent=subscription.agent,
            is_outbound=False,
            raw_payload__subscription_id=str(subscription.id),
            raw_payload__discord_message_id=message.message_id,
        )
        .order_by("timestamp", "id")
        .first()
    )


def _finalize_gateway_subscription_delivery(
    *,
    agent: PersistentAgent,
    subscription: PersistentAgentDiscordChannelSubscription,
    message: DiscordGatewayMessage,
    stored_message: PersistentAgentMessage,
) -> dict[str, Any]:
    display_name = f"#{message.channel_name.lstrip('#')}" if message.channel_name else f"Discord {message.channel_id}"
    if stored_message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=stored_message.conversation_id).update(display_name=display_name)
    inactive_blocked = (
        isinstance(stored_message.raw_payload, dict)
        and stored_message.raw_payload.get("inactive_handling") == INACTIVE_BLOCKED_INPUT_KIND
    )
    if inactive_blocked:
        transaction.on_commit(
            lambda: send_inactive_discord_auto_reply(
                agent,
                channel_id=message.channel_id,
                recipient_key=message.author_id or message.channel_id,
            ),
            robust=True,
        )
        debounce_result = {"debounced": False, "debounce_seconds": 0}
    else:
        debounce_result = schedule_discord_inbound_processing(
            str(agent.id),
            inbound_message_id=str(stored_message.id),
            typing_channel_id=message.channel_id,
        )
    subscription.record_message()
    return {
        "agent_id": str(agent.id),
        "subscription_id": str(subscription.id),
        "message_id": str(stored_message.id),
        "conversation_id": str(stored_message.conversation_id) if stored_message.conversation_id else "",
        "debounced": bool(debounce_result.get("debounced")),
        "debounce_seconds": debounce_result.get("debounce_seconds", 0),
        "processing_blocked_reason": "agent_inactive" if inactive_blocked else None,
    }


def _ingest_gateway_message_for_subscription(
    message: DiscordGatewayMessage,
    subscription: PersistentAgentDiscordChannelSubscription,
) -> dict[str, Any]:
    agent = subscription.agent
    existing_message = _existing_gateway_message_for_subscription(message, subscription)
    if existing_message is not None:
        return _finalize_gateway_subscription_delivery(
            agent=agent,
            subscription=subscription,
            message=message,
            stored_message=existing_message,
        )

    platform_channel_address = discord_channel_address(message.guild_id, message.channel_id)
    conversation_address = discord_conversation_address(agent.id, message.guild_id, message.channel_id)
    source_label = message.author_name or discord_channel_source_label(message.channel_id, message.channel_name)
    raw_payload = {
        "source": "discord_bot",
        "source_kind": "discord",
        "subscription_id": str(subscription.id),
        "discord_message_id": message.message_id,
        "discord_content": message.content,
        "discord_raw_content": message.raw_content,
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
    if message.reply_to:
        raw_payload["discord_reply_to"] = dict(message.reply_to)
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
    return _finalize_gateway_subscription_delivery(
        agent=agent,
        subscription=subscription,
        message=message,
        stored_message=info.message,
    )


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
        body=message.raw_content or message.content,
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
        "processing_blocked_reason": first_delivery.get("processing_blocked_reason"),
        "subscription_count": len(deliveries),
        "deliveries": deliveries,
        "skipped_subscription_ids": skipped_subscription_ids,
    }


def _agent_avatar_url(agent: PersistentAgent) -> str:
    avatar_url = build_public_agent_avatar_thumbnail_url(agent)
    if not avatar_url:
        return ""
    return avatar_url


def _agent_webhook_username(agent: PersistentAgent) -> str:
    base_name = (agent.name or "").strip() or "Agent"
    emotion, _expires_at = agent.get_active_emotion_state()
    suffix = f" {emotion}" if emotion else ""
    name_limit = DISCORD_WEBHOOK_USERNAME_MAX_LENGTH - len(suffix)
    return f"{base_name[:name_limit].rstrip() or 'Agent'}{suffix}"


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
    metadata: Mapping[str, object] | None = None,
    persisted_message: PersistentAgentMessage | None = None,
) -> PersistentAgentMessage:
    resolved_attachments = list(attachments or [])
    body = normalize_discord_markdown(decode_unicode_character_escapes(body))
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
    username = _agent_webhook_username(agent)
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
        **dict(metadata or {}),
    }
    if persisted_message is None:
        message = create_discord_outbound_message(
            agent,
            channel_id=subscription.channel_id,
            body=body,
            conversation_address=discord_conversation_address(agent.id, subscription.guild.guild_id, subscription.channel_id),
            platform_channel_address=discord_channel_address(subscription.guild.guild_id, subscription.channel_id),
            channel_name=subscription.channel_name,
            raw_payload=raw_payload,
        )
    else:
        message = persisted_message
        message.body = body
        message.raw_payload = raw_payload
        message.latest_status = DeliveryStatus.SENT
        message.latest_sent_at = timezone.now()
        message.latest_error_message = ""
        message.save(
            update_fields=[
                "body",
                "raw_payload",
                "latest_status",
                "latest_sent_at",
                "latest_error_message",
            ]
        )
    if resolved_attachments:
        create_message_attachments(message, resolved_attachments)
        broadcast_message_attachment_update(str(message.id))
    return message


def _prepare_inactive_discord_auto_reply(
    agent: PersistentAgent,
    *,
    channel_id: str,
    metadata: Mapping[str, object],
) -> Callable[[], bool]:
    subscription = (
        PersistentAgentDiscordChannelSubscription.objects.select_related("guild")
        .get(agent=agent, channel_id=channel_id, status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE)
    )
    body = inactive_auto_reply_body(agent)
    message = create_discord_outbound_message(
        agent,
        channel_id=subscription.channel_id,
        body=body,
        conversation_address=discord_conversation_address(agent.id, subscription.guild.guild_id, subscription.channel_id),
        platform_channel_address=discord_channel_address(subscription.guild.guild_id, subscription.channel_id),
        channel_name=subscription.channel_name,
        raw_payload={"kind": INACTIVE_AUTO_REPLY_KIND, **dict(metadata)},
        latest_status=DeliveryStatus.QUEUED,
    )

    def deliver() -> bool:
        try:
            send_channel_message(
                agent,
                channel_id=channel_id,
                body=body,
                metadata={"kind": INACTIVE_AUTO_REPLY_KIND, **dict(metadata)},
                persisted_message=message,
            )
        except (
            DiscordBotIntegrationError,
            PersistentAgentDiscordChannelSubscription.DoesNotExist,
            requests.RequestException,
        ) as exc:
            message.latest_status = DeliveryStatus.FAILED
            message.latest_error_message = str(exc)
            message.save(update_fields=["latest_status", "latest_error_message"])
            raise
        return True

    return deliver


def send_inactive_discord_auto_reply(
    agent: PersistentAgent,
    *,
    channel_id: str,
    recipient_key: str,
) -> bool:
    normalized_recipient_key = str(recipient_key or channel_id).strip()
    return send_inactive_notice_once(
        agent,
        channel=CommsChannel.DISCORD,
        recipient_key=normalized_recipient_key,
        prepare=lambda metadata: _prepare_inactive_discord_auto_reply(
            agent,
            channel_id=channel_id,
            metadata=metadata,
        ),
    )
