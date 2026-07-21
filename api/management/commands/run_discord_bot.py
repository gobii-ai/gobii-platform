"""Run the native Gobii Discord gateway bot."""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from api.services.discord_bot import DiscordGatewayMessage, ingest_gateway_message

logger = logging.getLogger(__name__)
DISCORD_REPLY_MESSAGE_TYPE = 19


def _attachment_payload(attachment) -> dict[str, object]:
    return {
        "id": str(getattr(attachment, "id", "") or ""),
        "filename": str(getattr(attachment, "filename", "") or ""),
        "url": str(getattr(attachment, "url", "") or ""),
        "proxy_url": str(getattr(attachment, "proxy_url", "") or ""),
        "content_type": str(getattr(attachment, "content_type", "") or ""),
        "size": int(getattr(attachment, "size", 0) or 0),
    }


def _embed_payload(embed) -> dict[str, object]:
    try:
        payload = embed.to_dict()
    except AttributeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _message_author(message) -> tuple[str, str]:
    author = getattr(message, "author", None)
    author_id = str(getattr(author, "id", "") or "")
    author_name = str(
        getattr(author, "display_name", "")
        or getattr(author, "name", "")
        or author_id
    )
    return author_id, author_name


def _message_content(message) -> tuple[str, str]:
    raw_content = str(getattr(message, "content", "") or "")
    return raw_content, str(getattr(message, "clean_content", "") or "") or raw_content


def _reply_reference_payload(message) -> dict[str, object] | None:
    message_type = getattr(getattr(message, "type", None), "value", None)
    if message_type != DISCORD_REPLY_MESSAGE_TYPE:
        return None

    reference = getattr(message, "reference", None)
    message_id = str(getattr(reference, "message_id", "") or "")
    if not reference or not message_id:
        return None

    resolved = getattr(reference, "resolved", None) or getattr(reference, "cached_message", None)
    unavailable = resolved is None or not hasattr(resolved, "content")
    author_id, author_name = _message_author(resolved) if not unavailable else ("", "")
    _raw_content, content = _message_content(resolved) if not unavailable else ("", "")
    return {
        "message_id": message_id,
        "channel_id": str(getattr(reference, "channel_id", "") or ""),
        "guild_id": str(getattr(reference, "guild_id", "") or ""),
        "author_id": author_id,
        "author_name": author_name,
        "content": content,
        "attachment_filenames": [
            str(getattr(attachment, "filename", "") or "")
            for attachment in (() if unavailable else (getattr(resolved, "attachments", None) or []))
            if str(getattr(attachment, "filename", "") or "")
        ],
        "unavailable": unavailable,
    }


def build_gateway_message(message) -> DiscordGatewayMessage:
    channel = message.channel
    guild = message.guild
    raw_content, content = _message_content(message)
    attachments = getattr(message, "attachments", None) or []
    embeds = getattr(message, "embeds", None) or []
    author_id, author_name = _message_author(message)
    return DiscordGatewayMessage(
        message_id=str(message.id),
        channel_id=str(channel.id),
        channel_name=str(getattr(channel, "name", "") or ""),
        guild_id=str(guild.id),
        guild_name=str(getattr(guild, "name", "") or ""),
        author_id=author_id,
        author_name=author_name,
        content=content,
        raw_content=raw_content,
        attachments=[_attachment_payload(attachment) for attachment in attachments],
        embeds=[payload for payload in (_embed_payload(embed) for embed in embeds) if payload],
        author_is_bot=bool(getattr(message.author, "bot", False)),
        webhook_id=str(getattr(message, "webhook_id", "") or ""),
        reply_to=_reply_reference_payload(message),
    )


def ingest_gateway_message_with_reconnect(gateway_message: DiscordGatewayMessage) -> dict:
    close_old_connections()
    try:
        return ingest_gateway_message(gateway_message)
    except OperationalError:
        logger.warning(
            "Discord bot DB connection failed while ingesting message %s; retrying with a fresh connection.",
            gateway_message.message_id,
            exc_info=True,
        )
        close_old_connections()
        return ingest_gateway_message(gateway_message)
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Run the native Gobii Discord bot gateway listener."

    def handle(self, *args, **options):
        if not settings.DISCORD_BOT_ENABLED:
            raise CommandError("DISCORD_BOT_ENABLED is false.")
        if not settings.DISCORD_BOT_TOKEN:
            raise CommandError("DISCORD_BOT_TOKEN is not configured.")

        try:
            import discord
        except ImportError as exc:
            raise CommandError("discord.py is not installed. Run uv sync after adding the dependency.") from exc

        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            user = client.user
            logger.info("Discord bot connected as %s (%s)", user, getattr(user, "id", ""))
            self.stdout.write(self.style.SUCCESS(f"Discord bot connected as {user}"))

        @client.event
        async def on_message(message):
            if message.guild is None:
                return
            if client.user is not None and message.author.id == client.user.id:
                return

            gateway_message = build_gateway_message(message)
            result = await sync_to_async(ingest_gateway_message_with_reconnect, thread_sensitive=True)(gateway_message)
            if not result.get("ignored"):
                logger.info(
                    "Ingested Discord message %s for channel %s",
                    gateway_message.message_id,
                    gateway_message.channel_id,
                )

        client.run(settings.DISCORD_BOT_TOKEN)
