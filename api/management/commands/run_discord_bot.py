"""Run the native Gobii Discord gateway bot."""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.services.discord_bot import DiscordGatewayMessage, ingest_gateway_message

logger = logging.getLogger(__name__)


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


def build_gateway_message(message) -> DiscordGatewayMessage:
    channel = message.channel
    guild = message.guild
    raw_content = str(getattr(message, "content", "") or "")
    clean_content = str(getattr(message, "clean_content", "") or "")
    attachments = getattr(message, "attachments", None) or []
    embeds = getattr(message, "embeds", None) or []
    author_name = str(
        getattr(message.author, "display_name", "")
        or getattr(message.author, "name", "")
        or getattr(message.author, "id", "")
    )
    return DiscordGatewayMessage(
        message_id=str(message.id),
        channel_id=str(channel.id),
        channel_name=str(getattr(channel, "name", "") or ""),
        guild_id=str(guild.id),
        guild_name=str(getattr(guild, "name", "") or ""),
        author_id=str(message.author.id),
        author_name=author_name,
        content=clean_content or raw_content,
        raw_content=raw_content,
        attachments=[_attachment_payload(attachment) for attachment in attachments],
        embeds=[payload for payload in (_embed_payload(embed) for embed in embeds) if payload],
        author_is_bot=bool(getattr(message.author, "bot", False)),
        webhook_id=str(getattr(message, "webhook_id", "") or ""),
    )


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
            result = await sync_to_async(ingest_gateway_message, thread_sensitive=True)(gateway_message)
            if not result.get("ignored"):
                logger.info(
                    "Ingested Discord message %s for channel %s",
                    gateway_message.message_id,
                    gateway_message.channel_id,
                )

        client.run(settings.DISCORD_BOT_TOKEN)
