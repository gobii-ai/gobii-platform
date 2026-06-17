"""Native Telegram managed bot integration."""

import hashlib
import hmac
import io
import logging
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlencode

import redis
import requests
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.files.attachment_helpers import ResolvedAttachment, create_message_attachments
from api.agent.files.filespace_service import broadcast_message_attachment_update
from api.agent.system_skills.defaults import TELEGRAM_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.models import (
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemStep,
    PersistentAgentTelegramBotIdentity,
    PersistentAgentTelegramChatBinding,
    PersistentAgentTelegramProvisioningSession,
    PersistentAgentTelegramUpdateReceipt,
    PersistentAgentTelegramUserLink,
    PersistentAgentTelegramUserLinkRequest,
)
from api.services.agent_avatar_public import build_public_agent_avatar_thumbnail_url
from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_LINK_SALT = "gobii.telegram.user_link"
TELEGRAM_LINK_MAX_AGE_SECONDS = 30 * 60
TELEGRAM_INBOUND_DEBOUNCE_DEADLINE_KEY = "agent:telegram-inbound-debounce:{agent_id}:deadline"
TELEGRAM_INBOUND_DEBOUNCE_SCHEDULED_KEY = "agent:telegram-inbound-debounce:{agent_id}:scheduled"
TELEGRAM_BOT_USERNAME_RE = re.compile(r"[^A-Za-z0-9_]+")


class TelegramBotIntegrationError(RuntimeError):
    """Raised when native Telegram setup or delivery cannot continue."""


@dataclass(frozen=True)
class TelegramConnectStartResult:
    status: str
    manager_link_url: str
    create_bot_url: str
    user_linked: bool
    suggested_username: str = ""
    suggested_name: str = ""
    message: str = ""


def _public_base_url() -> str:
    return settings.PUBLIC_SITE_URL.strip().rstrip("/")


def _agent_owner(agent: PersistentAgent) -> tuple[Any, Any]:
    if agent.organization_id:
        return None, agent.organization
    return agent.user, None


def _telegram_api_url(token: str, method: str) -> str:
    return f"{TELEGRAM_API_BASE}/bot{token}/{method}"


def _telegram_form_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        key: json.dumps(value, separators=(",", ":"), sort_keys=True) if isinstance(value, (dict, list)) else value
        for key, value in dict(payload or {}).items()
    }


def _telegram_request(token: str, method: str, *, payload: Mapping[str, Any] | None = None, files=None) -> dict[str, Any]:
    response = requests.post(
        _telegram_api_url(token, method),
        data=_telegram_form_payload(payload) if files else None,
        json=None if files else dict(payload or {}),
        files=files,
        timeout=20,
    )
    try:
        content = response.json() or {}
    except ValueError:
        content = {}
    if response.status_code < 200 or response.status_code >= 300 or not content.get("ok", response.ok):
        description = str(content.get("description") or response.text or "")[:1000]
        message = f"Telegram {method} failed with HTTP {response.status_code}."
        if description:
            message = f"{message} Response: {description}"
        raise TelegramBotIntegrationError(message)
    result = content.get("result")
    return result if isinstance(result, dict) else {"result": result}


def _manager_token() -> str:
    token = settings.TELEGRAM_MANAGER_BOT_TOKEN.strip()
    if not token:
        raise TelegramBotIntegrationError("TELEGRAM_MANAGER_BOT_TOKEN is not configured.")
    return token


def _manager_username() -> str:
    username = settings.TELEGRAM_MANAGER_BOT_USERNAME.strip().lstrip("@")
    if not username:
        raise TelegramBotIntegrationError("TELEGRAM_MANAGER_BOT_USERNAME is not configured.")
    return username


def ensure_manager_bot_can_manage_bots() -> dict[str, Any]:
    payload = _telegram_request(_manager_token(), "getMe")
    if not payload.get("can_manage_bots"):
        raise TelegramBotIntegrationError("Telegram manager bot cannot manage bots. Enable Bot Management Mode first.")
    return payload


def _link_token_signature(nonce: str) -> str:
    secret = settings.SECRET_KEY.encode("utf-8")
    message = f"{TELEGRAM_LINK_SALT}:{nonce}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:24]


def _generate_link_token() -> str:
    nonce = secrets.token_hex(12)
    return f"{nonce}{_link_token_signature(nonce)}"


def _create_link_request(agent: PersistentAgent, initiated_by) -> str:
    owner_user, organization = _agent_owner(agent)
    for _attempt in range(5):
        token = _generate_link_token()
        try:
            PersistentAgentTelegramUserLinkRequest.objects.create(
                token=token,
                agent=agent,
                owner_user=owner_user,
                organization=organization,
                initiated_by=initiated_by if getattr(initiated_by, "is_authenticated", False) else None,
                expires_at=timezone.now() + timedelta(seconds=TELEGRAM_LINK_MAX_AGE_SECONDS),
            )
            return token
        except IntegrityError:
            continue
    raise TelegramBotIntegrationError("Could not create a Telegram link token.")


def _manager_link_url_for_token(token: str) -> str:
    return f"https://t.me/{_manager_username()}?{urlencode({'start': token})}"


def build_telegram_manager_link_url(agent: PersistentAgent, initiated_by) -> str:
    return _manager_link_url_for_token(_create_link_request(agent, initiated_by))


def _latest_telegram_manager_link_url(agent: PersistentAgent) -> str:
    link_request = (
        PersistentAgentTelegramUserLinkRequest.objects
        .filter(agent=agent, used_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if link_request is None:
        return ""
    return _manager_link_url_for_token(link_request.token)


def validate_telegram_link_payload(token: str) -> dict[str, Any]:
    token = (token or "").strip()
    nonce = token[:24]
    signature = token[24:]
    expected = _link_token_signature(nonce)
    if len(token) != 48 or not hmac.compare_digest(signature, expected):
        raise TelegramBotIntegrationError("Telegram link token is invalid.")
    link_request = (
        PersistentAgentTelegramUserLinkRequest.objects
        .select_related("agent", "owner_user", "organization", "initiated_by")
        .filter(token=token, used_at__isnull=True, expires_at__gt=timezone.now())
        .first()
    )
    if link_request is None:
        raise TelegramBotIntegrationError("Telegram link token is invalid or expired.")
    return {
        "link_request_id": str(link_request.id),
        "agent_id": str(link_request.agent_id),
        "owner_user_id": link_request.owner_user_id,
        "organization_id": str(link_request.organization_id) if link_request.organization_id else None,
        "initiated_by_id": link_request.initiated_by_id,
    }


def upsert_telegram_user_link_from_start(payload: Mapping[str, Any], telegram_user: Mapping[str, Any]) -> dict[str, Any]:
    agent = PersistentAgent.objects.get(id=payload["agent_id"])
    owner_user, organization = _agent_owner(agent)
    if str(payload.get("owner_user_id") or "") != (str(owner_user.id) if owner_user else ""):
        raise TelegramBotIntegrationError("Telegram link owner did not match this agent.")
    if str(payload.get("organization_id") or "") != (str(organization.id) if organization else ""):
        raise TelegramBotIntegrationError("Telegram link organization did not match this agent.")

    telegram_user_id = str(telegram_user.get("id") or "").strip()
    if not telegram_user_id:
        raise TelegramBotIntegrationError("Telegram user id was not provided.")
    defaults = {
        "username": str(telegram_user.get("username") or "")[:255],
        "first_name": str(telegram_user.get("first_name") or "")[:255],
        "last_name": str(telegram_user.get("last_name") or "")[:255],
        "owner_user": owner_user,
        "organization": organization,
        "linked_by_id": payload.get("initiated_by_id") or None,
        "is_active": True,
        "last_seen_at": timezone.now(),
    }
    link, _created = PersistentAgentTelegramUserLink.objects.update_or_create(
        owner_user=owner_user,
        organization=organization,
        telegram_user_id=telegram_user_id,
        defaults=defaults,
    )
    link_request_id = payload.get("link_request_id")
    if link_request_id:
        PersistentAgentTelegramUserLinkRequest.objects.filter(id=link_request_id, used_at__isnull=True).update(
            used_at=timezone.now(),
            updated_at=timezone.now(),
        )
    return {"status": "success", "telegram_user_id": link.telegram_user_id}


def _current_telegram_user_link(agent: PersistentAgent) -> PersistentAgentTelegramUserLink | None:
    owner_user, organization = _agent_owner(agent)
    queryset = PersistentAgentTelegramUserLink.objects.filter(is_active=True)
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    else:
        queryset = queryset.filter(owner_user=owner_user, organization__isnull=True)
    return queryset.order_by("-last_seen_at", "-updated_at").first()


def _suggested_bot_name(agent: PersistentAgent) -> str:
    name = (agent.name or "Gobii Agent").strip()
    return name[:64] or "Gobii Agent"


def _suggested_bot_username(agent: PersistentAgent) -> str:
    base = TELEGRAM_BOT_USERNAME_RE.sub("_", (agent.name or "gobii_agent").strip()).strip("_").lower()
    if not base:
        base = "gobii_agent"
    if len(base) < 5:
        base = f"{base}_agent"
    suffix = str(agent.id).replace("-", "")[:10]
    username = f"{base}_{suffix}_bot"
    if len(username) > 32:
        username = f"{base[: max(1, 32 - len(suffix) - 5)]}_{suffix}_bot"
    if not username.lower().endswith("bot"):
        username = f"{username}_bot"
    return username


def _managed_bot_create_url(suggested_username: str, suggested_name: str) -> str:
    manager = _manager_username()
    path = f"https://t.me/newbot/{quote(manager)}/{quote(suggested_username)}"
    return f"{path}?{urlencode({'name': suggested_name})}"


def start_telegram_connect(agent: PersistentAgent, initiated_by) -> TelegramConnectStartResult:
    ensure_manager_bot_can_manage_bots()
    user_link = _current_telegram_user_link(agent)
    if user_link is None:
        manager_link_url = build_telegram_manager_link_url(agent, initiated_by)
        return TelegramConnectStartResult(
            status="link_required",
            manager_link_url=manager_link_url,
            create_bot_url="",
            user_linked=False,
            message="Open Telegram and link your account to Gobii before creating the agent bot.",
        )

    owner_user, organization = _agent_owner(agent)
    suggested_username = _suggested_bot_username(agent)
    suggested_name = _suggested_bot_name(agent)
    with transaction.atomic():
        PersistentAgentTelegramProvisioningSession.objects.select_for_update().filter(
            user_link=user_link,
            status=PersistentAgentTelegramProvisioningSession.Status.PENDING,
        ).update(status=PersistentAgentTelegramProvisioningSession.Status.CANCELED, updated_at=timezone.now())
        PersistentAgentTelegramProvisioningSession.objects.create(
            agent=agent,
            user_link=user_link,
            owner_user=owner_user,
            organization=organization,
            initiated_by=initiated_by if getattr(initiated_by, "is_authenticated", False) else None,
            suggested_username=suggested_username,
            suggested_name=suggested_name,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
    return TelegramConnectStartResult(
        status="create_required",
        manager_link_url=_latest_telegram_manager_link_url(agent),
        create_bot_url=_managed_bot_create_url(suggested_username, suggested_name),
        user_linked=True,
        suggested_username=suggested_username,
        suggested_name=suggested_name,
        message="Create the managed Telegram bot for this agent.",
    )


def _extract_managed_bot_user_id(update: Mapping[str, Any]) -> str:
    managed_bot = update.get("managed_bot")
    if isinstance(managed_bot, Mapping):
        for key in ("user", "from", "creator", "owner"):
            value = managed_bot.get(key)
            if isinstance(value, Mapping) and value.get("id"):
                return str(value.get("id")).strip()
        if managed_bot.get("user_id"):
            return str(managed_bot.get("user_id")).strip()
    return ""


def _extract_managed_bot_payload(update: Mapping[str, Any]) -> Mapping[str, Any]:
    managed_bot = update.get("managed_bot")
    if not isinstance(managed_bot, Mapping):
        raise TelegramBotIntegrationError("Telegram managed_bot update was not provided.")
    for key in ("bot", "managed_bot", "result"):
        value = managed_bot.get(key)
        if isinstance(value, Mapping) and value.get("id"):
            return value
    if managed_bot.get("id"):
        return managed_bot
    raise TelegramBotIntegrationError("Telegram managed_bot update did not include bot details.")


def _fetch_managed_bot_token(bot_id: str) -> str:
    result = _telegram_request(_manager_token(), "getManagedBotToken", payload={"user_id": bot_id})
    token = str(result.get("token") or result.get("result") or "").strip()
    if not token:
        raise TelegramBotIntegrationError("Telegram did not return a managed bot token.")
    return token


def _agent_bot_webhook_url(identity: PersistentAgentTelegramBotIdentity) -> str:
    path = reverse("telegram_agent_bot_webhook", args=[identity.id])
    return f"{_public_base_url()}{path}"


def _manager_webhook_url() -> str:
    path = reverse("telegram_manager_webhook")
    return f"{_public_base_url()}{path}"


def configure_telegram_manager_webhook() -> dict[str, Any]:
    payload: dict[str, Any] = {"url": _manager_webhook_url()}
    if settings.TELEGRAM_MANAGER_WEBHOOK_SECRET:
        payload["secret_token"] = settings.TELEGRAM_MANAGER_WEBHOOK_SECRET
    return _telegram_request(_manager_token(), "setWebhook", payload=payload)


def send_telegram_manager_message(chat_id: str, text: str) -> bool:
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return False
    try:
        _telegram_request(_manager_token(), "sendMessage", payload={"chat_id": chat_id, "text": text})
        return True
    except (TelegramBotIntegrationError, requests.RequestException):
        logger.warning("Failed to send Telegram manager bot message to chat %s.", chat_id, exc_info=True)
        return False


def _set_agent_bot_webhook(identity: PersistentAgentTelegramBotIdentity) -> None:
    secret_token = identity.webhook_secret
    if not secret_token:
        raise TelegramBotIntegrationError("Telegram bot webhook secret is missing.")
    _telegram_request(
        identity.token,
        "setWebhook",
        payload={"url": _agent_bot_webhook_url(identity), "secret_token": secret_token},
    )


def _delete_agent_bot_webhook(identity: PersistentAgentTelegramBotIdentity) -> None:
    token = identity.token
    if token:
        _telegram_request(token, "deleteWebhook", payload={"drop_pending_updates": False})


def _telegram_profile_photo_jpeg(content: bytes) -> bytes:
    with Image.open(io.BytesIO(content)) as image:
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        background.alpha_composite(image)
        rgb_image = background.convert("RGB")
        output = io.BytesIO()
        rgb_image.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue()


def _sync_telegram_bot_avatar(identity: PersistentAgentTelegramBotIdentity, avatar_url: str) -> None:
    response = requests.get(avatar_url, timeout=20)
    response.raise_for_status()
    jpeg = _telegram_profile_photo_jpeg(response.content)
    _telegram_request(
        identity.token,
        "setMyProfilePhoto",
        payload={"photo": {"type": "static", "photo": "attach://photo"}},
        files={"photo": ("avatar.jpg", jpeg, "image/jpeg")},
    )


def sync_telegram_bot_profile(identity: PersistentAgentTelegramBotIdentity) -> dict[str, Any]:
    agent = identity.agent
    token = identity.token
    if not token:
        raise TelegramBotIntegrationError("Telegram bot token is missing.")
    try:
        display_name = _suggested_bot_name(agent)
        _telegram_request(token, "setMyName", payload={"name": display_name})
        description = (agent.short_description or agent.mini_description or agent.charter or "")[:512]
        if description:
            _telegram_request(token, "setMyDescription", payload={"description": description})
            _telegram_request(token, "setMyShortDescription", payload={"short_description": description[:120]})
        avatar_error = ""
        avatar_url = build_public_agent_avatar_thumbnail_url(agent)
        if avatar_url:
            try:
                _sync_telegram_bot_avatar(identity, avatar_url)
            except (TelegramBotIntegrationError, requests.RequestException, OSError, UnidentifiedImageError) as exc:
                avatar_error = f"Telegram avatar sync skipped: {exc}"
                logger.warning("Failed to sync Telegram avatar for bot identity %s.", identity.id, exc_info=True)
        identity.display_name = display_name
        identity.profile_sync_status = PersistentAgentTelegramBotIdentity.SyncStatus.SYNCED
        identity.profile_synced_at = timezone.now()
        identity.profile_sync_error = avatar_error[:2000]
        identity.save(update_fields=[
            "display_name",
            "profile_sync_status",
            "profile_synced_at",
            "profile_sync_error",
            "updated_at",
        ])
        return {"status": "success"}
    except (TelegramBotIntegrationError, requests.RequestException) as exc:
        identity.profile_sync_status = PersistentAgentTelegramBotIdentity.SyncStatus.ERROR
        identity.profile_sync_error = str(exc)[:2000]
        identity.save(update_fields=["profile_sync_status", "profile_sync_error", "updated_at"])
        return {"status": "error", "message": str(exc)}


def _set_agent_bot_commands(identity: PersistentAgentTelegramBotIdentity) -> None:
    _telegram_request(
        identity.token,
        "setMyCommands",
        payload={
            "commands": [
                {"command": "start", "description": "Start a conversation"},
                {"command": "help", "description": "Show how to talk to this agent"},
            ]
        },
    )


def complete_managed_bot_provisioning(update: Mapping[str, Any]) -> dict[str, Any]:
    telegram_user_id = _extract_managed_bot_user_id(update)
    if not telegram_user_id:
        return {"ignored": True, "reason": "missing_creator_user"}
    bot_payload = _extract_managed_bot_payload(update)
    bot_id = str(bot_payload.get("id") or "").strip()
    username = str(bot_payload.get("username") or "").strip().lstrip("@")
    display_name = str(bot_payload.get("first_name") or bot_payload.get("name") or "").strip()
    if not bot_id or not username:
        raise TelegramBotIntegrationError("Telegram managed bot update did not include bot id and username.")

    now = timezone.now()
    sessions = list(
        PersistentAgentTelegramProvisioningSession.objects.select_related("agent", "user_link")
        .filter(
            user_link__telegram_user_id=telegram_user_id,
            user_link__is_active=True,
            status=PersistentAgentTelegramProvisioningSession.Status.PENDING,
            expires_at__gt=now,
        )
        .order_by("created_at")
    )
    if len(sessions) != 1:
        return {"ignored": True, "reason": "ambiguous_session", "session_count": len(sessions)}

    session = sessions[0]
    token = _fetch_managed_bot_token(bot_id)
    with transaction.atomic():
        identity, _created = PersistentAgentTelegramBotIdentity.objects.select_for_update().update_or_create(
            agent=session.agent,
            defaults={
                "provisioning_session": session,
                "telegram_bot_id": bot_id,
                "username": username,
                "display_name": display_name or session.suggested_name,
                "status": PersistentAgentTelegramBotIdentity.Status.ACTIVE,
                "connected_at": now,
                "disconnected_at": None,
                "last_error": "",
            },
        )
        identity.token = token
        identity.webhook_secret = secrets.token_urlsafe(32)
        identity.save(update_fields=[
            "token_encrypted",
            "webhook_secret_encrypted",
            "provisioning_session",
            "telegram_bot_id",
            "username",
            "display_name",
            "status",
            "connected_at",
            "disconnected_at",
            "last_error",
            "updated_at",
        ])
        session.status = PersistentAgentTelegramProvisioningSession.Status.COMPLETED
        session.completed_at = now
        session.last_error = ""
        session.save(update_fields=["status", "completed_at", "last_error", "updated_at"])

    _telegram_request(identity.token, "getMe")
    _set_agent_bot_webhook(identity)
    _set_agent_bot_commands(identity)
    sync_telegram_bot_profile(identity)
    enable_system_skills(session.agent, [TELEGRAM_NATIVE_SYSTEM_SKILL_KEY])
    _queue_telegram_connection_step(identity)
    return {"ignored": False, "agent_id": str(session.agent_id), "bot_id": bot_id, "username": username}


def _queue_telegram_connection_step(identity: PersistentAgentTelegramBotIdentity) -> None:
    bot_label = f"@{identity.username}" if identity.username else "the managed Telegram bot"
    step = PersistentAgentStep.objects.create(
        agent=identity.agent,
        description=(
            f"Telegram connection completed through {bot_label}. "
            "This agent can now receive private Telegram DMs and group commands, mentions, or replies delivered to the bot. "
            "Use telegram_chats with action=\"status\" or action=\"list\" when checking Telegram setup, "
            "and use send_telegram_message to reply to known Telegram chats when appropriate."
        ),
    )
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
        notes=json.dumps(
            {
                "source": "telegram_managed_bot",
                "bot_id": identity.telegram_bot_id,
                "bot_username": identity.username,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )

    def _trigger_processing() -> None:
        from api.agent.tasks.process_events import process_agent_events_task

        process_agent_events_task.delay(str(identity.agent_id))

    transaction.on_commit(_trigger_processing)


def telegram_agent_address(identity: PersistentAgentTelegramBotIdentity) -> str:
    return f"telegram://bot/{identity.telegram_bot_id}/agent/{identity.agent_id}"


def telegram_chat_address(chat_id: str) -> str:
    return f"telegram://chat/{chat_id}"


def telegram_conversation_address(identity: PersistentAgentTelegramBotIdentity, chat_id: str, thread_id: str = "") -> str:
    address = f"telegram://bot/{identity.telegram_bot_id}/chat/{chat_id}"
    if thread_id:
        address = f"{address}/thread/{thread_id}"
    return address


def ensure_telegram_agent_endpoint(identity: PersistentAgentTelegramBotIdentity) -> PersistentAgentCommsEndpoint:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.TELEGRAM,
        address=telegram_agent_address(identity),
        defaults={"owner_agent": identity.agent, "is_primary": True},
    )
    updates = []
    if endpoint.owner_agent_id != identity.agent_id:
        endpoint.owner_agent = identity.agent
        updates.append("owner_agent")
    if not endpoint.is_primary:
        endpoint.is_primary = True
        updates.append("is_primary")
    if updates:
        endpoint.save(update_fields=updates)
    return endpoint


def _telegram_chat_endpoint(chat_id: str) -> PersistentAgentCommsEndpoint:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.TELEGRAM,
        address=telegram_chat_address(chat_id),
        defaults={"owner_agent": None},
    )
    return endpoint


def _ensure_conversation_participant(conversation, endpoint, role: str) -> None:
    PersistentAgentConversationParticipant.objects.get_or_create(
        conversation=conversation,
        endpoint=endpoint,
        defaults={"role": role},
    )


def _display_name_for_chat(chat: Mapping[str, Any], chat_id: str) -> str:
    title = str(chat.get("title") or "").strip()
    if title:
        return title
    full_name = " ".join(
        part for part in [
            str(chat.get("first_name") or "").strip(),
            str(chat.get("last_name") or "").strip(),
        ]
        if part
    )
    username = str(chat.get("username") or "").strip()
    return full_name or (f"@{username}" if username else f"Telegram {chat_id}")


def _get_or_create_telegram_conversation(
    identity: PersistentAgentTelegramBotIdentity,
    *,
    chat_id: str,
    message_thread_id: str = "",
    display_name: str = "",
) -> PersistentAgentConversation:
    conversation, created = PersistentAgentConversation.objects.get_or_create(
        channel=CommsChannel.TELEGRAM,
        address=telegram_conversation_address(identity, chat_id, message_thread_id),
        defaults={"owner_agent": identity.agent, "display_name": display_name},
    )
    updates = []
    if conversation.owner_agent_id is None:
        conversation.owner_agent = identity.agent
        updates.append("owner_agent")
    if display_name and conversation.display_name != display_name:
        conversation.display_name = display_name
        updates.append("display_name")
    if updates and not created:
        conversation.save(update_fields=updates)
    return conversation


def _ensure_chat_binding(
    identity: PersistentAgentTelegramBotIdentity,
    chat: Mapping[str, Any],
    *,
    message_thread_id: str = "",
) -> PersistentAgentTelegramChatBinding:
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id:
        raise TelegramBotIntegrationError("Telegram chat id was missing.")
    display_name = _display_name_for_chat(chat, chat_id)
    binding, _created = PersistentAgentTelegramChatBinding.objects.update_or_create(
        bot_identity=identity,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        defaults={
            "agent": identity.agent,
            "chat_type": str(chat.get("type") or "")[:32],
            "title": display_name[:255],
            "username": str(chat.get("username") or "")[:255],
            "status": PersistentAgentTelegramChatBinding.Status.ACTIVE,
        },
    )
    return binding


def _telegram_inbound_debounce_seconds() -> int:
    return max(0, int(settings.TELEGRAM_INBOUND_DEBOUNCE_SECONDS))


def _telegram_inbound_debounce_ttl(delay_seconds: int) -> int:
    return max(60, delay_seconds * 6)


def _process_agent_events_after_telegram_debounce(agent_id: str, *, countdown: int = 0) -> None:
    from api.agent.tasks import process_agent_events_task

    if countdown > 0:
        process_agent_events_task.apply_async(args=[agent_id], countdown=countdown)
    else:
        process_agent_events_task.delay(agent_id)


def schedule_telegram_inbound_processing(agent_id: str) -> dict[str, object]:
    debounce_seconds = _telegram_inbound_debounce_seconds()
    if debounce_seconds <= 0:
        _process_agent_events_after_telegram_debounce(str(agent_id))
        return {"debounced": False, "debounce_seconds": 0, "scheduled": True}
    normalized_agent_id = str(agent_id)
    deadline_key = TELEGRAM_INBOUND_DEBOUNCE_DEADLINE_KEY.format(agent_id=normalized_agent_id)
    scheduled_key = TELEGRAM_INBOUND_DEBOUNCE_SCHEDULED_KEY.format(agent_id=normalized_agent_id)
    deadline = time.time() + debounce_seconds
    ttl = _telegram_inbound_debounce_ttl(debounce_seconds)
    try:
        redis_client = get_redis_client()
        pipeline = redis_client.pipeline(transaction=True)
        pipeline.set(deadline_key, f"{deadline:.6f}", ex=ttl)
        pipeline.set(scheduled_key, "1", ex=ttl, nx=True)
        results = pipeline.execute()
        scheduled = bool(results[1])
    except redis.exceptions.RedisError:
        logger.exception("Failed scheduling Telegram inbound debounce for agent %s.", normalized_agent_id)
        _process_agent_events_after_telegram_debounce(normalized_agent_id, countdown=debounce_seconds)
        return {"debounced": False, "debounce_seconds": debounce_seconds, "scheduled": True, "fallback": True}
    if scheduled:
        if settings.CELERY_TASK_ALWAYS_EAGER:
            redis_client.delete(deadline_key, scheduled_key)
            _process_agent_events_after_telegram_debounce(normalized_agent_id)
            return {"debounced": False, "debounce_seconds": debounce_seconds, "scheduled": True, "eager": True}
        from api.agent.tasks.process_events import process_telegram_inbound_debounce_task

        process_telegram_inbound_debounce_task.apply_async(args=[normalized_agent_id], countdown=debounce_seconds)
    return {"debounced": True, "debounce_seconds": debounce_seconds, "scheduled": scheduled}


def _coerce_redis_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def process_telegram_inbound_debounce(agent_id: str) -> None:
    debounce_seconds = _telegram_inbound_debounce_seconds()
    normalized_agent_id = str(agent_id)
    if debounce_seconds <= 0:
        _process_agent_events_after_telegram_debounce(normalized_agent_id)
        return
    deadline_key = TELEGRAM_INBOUND_DEBOUNCE_DEADLINE_KEY.format(agent_id=normalized_agent_id)
    scheduled_key = TELEGRAM_INBOUND_DEBOUNCE_SCHEDULED_KEY.format(agent_id=normalized_agent_id)
    now = time.time()
    try:
        redis_client = get_redis_client()
        deadline = _coerce_redis_float(redis_client.get(deadline_key))
        if deadline is not None and deadline > now:
            if settings.CELERY_TASK_ALWAYS_EAGER:
                redis_client.delete(deadline_key, scheduled_key)
                _process_agent_events_after_telegram_debounce(normalized_agent_id)
                return
            countdown = max(1, math.ceil(deadline - now))
            ttl = _telegram_inbound_debounce_ttl(max(debounce_seconds, countdown))
            redis_client.expire(deadline_key, ttl)
            redis_client.expire(scheduled_key, ttl)
            from api.agent.tasks.process_events import process_telegram_inbound_debounce_task

            process_telegram_inbound_debounce_task.apply_async(args=[normalized_agent_id], countdown=countdown)
            return
        redis_client.delete(deadline_key, scheduled_key)
    except redis.exceptions.RedisError:
        logger.exception("Failed processing Telegram inbound debounce for agent %s.", normalized_agent_id)
    _process_agent_events_after_telegram_debounce(normalized_agent_id)


def _attachment_downloads(message: Mapping[str, Any]) -> list[dict[str, str]]:
    # Telegram file URLs require a bot-token getFile lookup, so V1 stores provider metadata
    # for auditing and outbound context. Files can be expanded later without changing routing.
    downloads = []
    for key in ("document", "photo", "video", "audio", "voice"):
        value = message.get(key)
        if not value:
            continue
        if isinstance(value, list) and value:
            value = value[-1]
        if isinstance(value, Mapping):
            item = {"telegram_file_id": str(value.get("file_id") or "")}
            filename = str(value.get("file_name") or key).strip()
            if filename:
                item["filename"] = filename
            downloads.append(item)
    return downloads


def _message_text(message: Mapping[str, Any]) -> str:
    text = str(message.get("text") or message.get("caption") or "").strip()
    if text:
        return text
    attachments = _attachment_downloads(message)
    if attachments:
        names = ", ".join(item.get("filename") or "attachment" for item in attachments)
        return f"Telegram attachment: {names}"
    return ""


def ingest_agent_bot_update(identity: PersistentAgentTelegramBotIdentity, update: Mapping[str, Any]) -> dict[str, Any]:
    if identity.status != PersistentAgentTelegramBotIdentity.Status.ACTIVE:
        return {"ignored": True, "reason": "inactive_bot"}
    update_id_raw = update.get("update_id")
    try:
        update_id = int(update_id_raw)
    except (TypeError, ValueError):
        return {"ignored": True, "reason": "missing_update_id"}
    try:
        PersistentAgentTelegramUpdateReceipt.objects.create(bot_identity=identity, update_id=update_id)
    except IntegrityError:
        return {"ignored": True, "reason": "duplicate_update"}
    PersistentAgentTelegramBotIdentity.objects.filter(id=identity.id).update(last_update_id=update_id)

    message = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not isinstance(message, Mapping):
        return {"ignored": True, "reason": "unsupported_update"}
    sender = message.get("from") if isinstance(message.get("from"), Mapping) else {}
    if sender and sender.get("is_bot"):
        return {"ignored": True, "reason": "bot_sender"}
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        return {"ignored": True, "reason": "missing_chat"}
    chat_id = str(chat.get("id") or "").strip()
    message_id = str(message.get("message_id") or "").strip()
    thread_id = str(message.get("message_thread_id") or "").strip()
    body = _message_text(message)
    if not body:
        return {"ignored": True, "reason": "empty_message"}

    binding = _ensure_chat_binding(identity, chat, message_thread_id=thread_id)
    if binding.status != PersistentAgentTelegramChatBinding.Status.ACTIVE:
        return {"ignored": True, "reason": "disabled_chat"}
    display_name = binding.title or _display_name_for_chat(chat, chat_id)
    conversation_address = telegram_conversation_address(identity, chat_id, thread_id)
    raw_payload = {
        "source": "telegram_bot",
        "source_kind": "telegram",
        "telegram_update_id": update_id,
        "telegram_message_id": message_id,
        "telegram_bot_id": identity.telegram_bot_id,
        "telegram_bot_username": identity.username,
        "telegram_chat_id": chat_id,
        "telegram_chat_type": str(chat.get("type") or ""),
        "telegram_message_thread_id": thread_id,
        "telegram_from_id": str(sender.get("id") or ""),
        "telegram_from_username": str(sender.get("username") or ""),
        "telegram_chat_title": display_name,
        "telegram_conversation_address": conversation_address,
        "source_label": display_name,
    }
    parsed = ParsedMessage(
        sender=telegram_chat_address(chat_id),
        recipient=telegram_agent_address(identity),
        subject=None,
        body=body,
        attachments=[],
        raw_payload=raw_payload,
        msg_channel=CommsChannel.TELEGRAM.value,
        conversation_address=conversation_address,
    )
    ensure_telegram_agent_endpoint(identity)
    info = ingest_inbound_message(
        CommsChannel.TELEGRAM,
        parsed,
        filespace_import_mode="sync",
        trigger_processing=False,
    )
    if info.message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=info.message.conversation_id).update(display_name=display_name)
    debounce_result = schedule_telegram_inbound_processing(str(identity.agent_id))
    binding.record_message()
    return {
        "ignored": False,
        "agent_id": str(identity.agent_id),
        "chat_binding_id": str(binding.id),
        "message_id": str(info.message.id),
        "conversation_id": str(info.message.conversation_id) if info.message.conversation_id else "",
        "debounced": bool(debounce_result.get("debounced")),
        "debounce_seconds": debounce_result.get("debounce_seconds", 0),
    }


def serialize_chat_binding(binding: PersistentAgentTelegramChatBinding) -> dict[str, str]:
    return {
        "id": str(binding.id),
        "agent_id": str(binding.agent_id),
        "chat_id": binding.chat_id,
        "chat_type": binding.chat_type,
        "message_thread_id": binding.message_thread_id,
        "title": binding.title,
        "username": binding.username,
        "status": binding.status,
        "last_message_at": binding.last_message_at.isoformat() if binding.last_message_at else "",
    }


def list_chat_bindings(agent: PersistentAgent) -> list[dict[str, str]]:
    bindings = PersistentAgentTelegramChatBinding.objects.filter(agent=agent).order_by("title", "chat_id")
    return [serialize_chat_binding(binding) for binding in bindings]


def disable_chat_binding(agent: PersistentAgent, binding_id: str) -> dict[str, str]:
    binding = PersistentAgentTelegramChatBinding.objects.get(id=binding_id, agent=agent)
    binding.status = PersistentAgentTelegramChatBinding.Status.DISABLED
    binding.save(update_fields=["status", "updated_at"])
    return serialize_chat_binding(binding)


def active_telegram_identity(agent: PersistentAgent) -> PersistentAgentTelegramBotIdentity | None:
    return (
        PersistentAgentTelegramBotIdentity.objects
        .filter(agent=agent, status=PersistentAgentTelegramBotIdentity.Status.ACTIVE)
        .first()
    )


def telegram_setup_required_response(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "status": "action_required",
        "message": "Connect Telegram to create a managed Telegram bot identity for this agent.",
        "connect_url": reverse("console-agent-telegram-connect", args=[agent.id]),
        "chats": [],
    }


def create_telegram_outbound_message(
    identity: PersistentAgentTelegramBotIdentity,
    *,
    binding: PersistentAgentTelegramChatBinding,
    body: str,
    raw_payload: Mapping[str, object] | None = None,
) -> PersistentAgentMessage:
    conversation = _get_or_create_telegram_conversation(
        identity,
        chat_id=binding.chat_id,
        message_thread_id=binding.message_thread_id,
        display_name=binding.title,
    )
    from_endpoint = ensure_telegram_agent_endpoint(identity)
    channel_endpoint = _telegram_chat_endpoint(binding.chat_id)
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
    payload = dict(raw_payload or {})
    payload.setdefault("source_kind", "telegram")
    payload.setdefault("telegram_chat_id", binding.chat_id)
    payload.setdefault("telegram_message_thread_id", binding.message_thread_id)
    payload.setdefault("telegram_bot_id", identity.telegram_bot_id)
    payload.setdefault("telegram_bot_username", identity.username)
    payload.setdefault("source_label", binding.title)
    return PersistentAgentMessage.objects.create(
        owner_agent=identity.agent,
        from_endpoint=from_endpoint,
        conversation=conversation,
        is_outbound=True,
        body=body,
        raw_payload=payload,
        latest_status=DeliveryStatus.SENT,
        latest_sent_at=timezone.now(),
    )


def send_chat_message(
    agent: PersistentAgent,
    *,
    chat_binding_id: str = "",
    chat_id: str = "",
    message_thread_id: str = "",
    body: str,
    attachments: Iterable[ResolvedAttachment] | None = None,
) -> PersistentAgentMessage:
    identity = active_telegram_identity(agent)
    if identity is None:
        raise TelegramBotIntegrationError("No active Telegram bot is connected for this agent.")
    queryset = PersistentAgentTelegramChatBinding.objects.filter(
        agent=agent,
        bot_identity=identity,
        status=PersistentAgentTelegramChatBinding.Status.ACTIVE,
    )
    if chat_binding_id:
        binding = queryset.get(id=chat_binding_id)
    else:
        binding = queryset.get(chat_id=chat_id, message_thread_id=message_thread_id)
    payload: dict[str, Any] = {"chat_id": binding.chat_id, "text": body}
    if binding.message_thread_id:
        try:
            payload["message_thread_id"] = int(binding.message_thread_id)
        except ValueError:
            payload["message_thread_id"] = binding.message_thread_id
    result = _telegram_request(identity.token, "sendMessage", payload=payload)
    raw_payload = {
        "source": "telegram_bot",
        "telegram_message_id": str(result.get("message_id") or ""),
        "telegram_chat_id": binding.chat_id,
        "telegram_message_thread_id": binding.message_thread_id,
    }
    stored = create_telegram_outbound_message(identity, binding=binding, body=body, raw_payload=raw_payload)
    resolved_attachments = list(attachments or [])
    if resolved_attachments:
        create_message_attachments(stored, resolved_attachments)
        broadcast_message_attachment_update(agent, stored)
    return stored


def disconnect_telegram_native_integration(agent: PersistentAgent) -> dict[str, int]:
    now = timezone.now()
    identity = PersistentAgentTelegramBotIdentity.objects.filter(agent=agent).first()
    webhook_removed = 0
    if identity and identity.status == PersistentAgentTelegramBotIdentity.Status.ACTIVE:
        try:
            _delete_agent_bot_webhook(identity)
            webhook_removed = 1
        except TelegramBotIntegrationError as exc:
            identity.last_error = str(exc)[:2000]
        identity.status = PersistentAgentTelegramBotIdentity.Status.DISCONNECTED
        identity.disconnected_at = now
        identity.save(update_fields=["status", "disconnected_at", "last_error", "updated_at"])
    bindings_disabled = PersistentAgentTelegramChatBinding.objects.filter(
        agent=agent,
    ).exclude(status=PersistentAgentTelegramChatBinding.Status.DISABLED).update(
        status=PersistentAgentTelegramChatBinding.Status.DISABLED,
        updated_at=now,
    )
    skills_disabled = PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key=TELEGRAM_NATIVE_SYSTEM_SKILL_KEY,
        is_enabled=True,
    ).update(is_enabled=False)
    return {
        "bot_disconnected": 1 if identity else 0,
        "webhook_removed": webhook_removed,
        "chat_bindings_disabled": bindings_disabled,
        "agents_disabled": skills_disabled,
    }


def serialize_telegram_app(agent: PersistentAgent) -> dict[str, Any]:
    identity = PersistentAgentTelegramBotIdentity.objects.filter(agent=agent).first()
    active_identity = identity if identity and identity.status == PersistentAgentTelegramBotIdentity.Status.ACTIVE else None
    chats = list_chat_bindings(agent)
    active_chats = [chat for chat in chats if chat.get("status") == PersistentAgentTelegramChatBinding.Status.ACTIVE]
    user_link = _current_telegram_user_link(agent)
    skill_enabled = PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key=TELEGRAM_NATIVE_SYSTEM_SKILL_KEY,
        is_enabled=True,
    ).exists()
    pending_session = (
        PersistentAgentTelegramProvisioningSession.objects.filter(
            agent=agent,
            status=PersistentAgentTelegramProvisioningSession.Status.PENDING,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )
    return {
        "provider_key": "telegram",
        "display_name": "Telegram",
        "description": "Create a managed Telegram bot identity for this agent.",
        "icon": "telegram",
        "native": True,
        "connected": bool(active_identity),
        "subscribed": bool(active_chats),
        "skill_enabled": skill_enabled,
        "user_linked": bool(user_link),
        "status": active_identity.status if active_identity else ("pending" if pending_session else "disconnected"),
        "bot_username": active_identity.username if active_identity else "",
        "bot_display_name": active_identity.display_name if active_identity else "",
        "profile_sync_status": active_identity.profile_sync_status if active_identity else "",
        "profile_sync_error": active_identity.profile_sync_error if active_identity else "",
        "manager_link_url": "" if user_link else _latest_telegram_manager_link_url(agent),
        "create_bot_url": _managed_bot_create_url(pending_session.suggested_username, pending_session.suggested_name) if pending_session else "",
        "chats": chats,
        "active_chat_count": len(active_chats),
    }
