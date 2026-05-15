import json
import os
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.agent.system_skills.registry import get_system_skill_definition
from api.agent.tools.discord_channel_subscriptions import execute_discord_channel_subscriptions
from api.agent.tools.discord_send_message import execute_discord_send_message
from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentDiscordChannelSubscription,
    PersistentAgentDiscordGuild,
    PersistentAgentDiscordOAuthSession,
    PersistentAgentDiscordWebhook,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
)
from api.services.discord_bot import (
    DiscordGatewayMessage,
    discover_channels,
    ensure_subscription,
    handle_discord_oauth_callback,
    ingest_gateway_message,
    send_channel_message,
    start_discord_oauth,
)


def _response(payload=None, status_code=200, content=b""):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    response.content = content
    response.headers = {"Content-Type": "text/plain", "Content-Length": str(len(content))}
    response.raise_for_status.return_value = None
    return response


@override_settings(
    DISCORD_CLIENT_ID="discord-client",
    DISCORD_CLIENT_SECRET="discord-secret",
    DISCORD_BOT_TOKEN="discord-bot-token",
    DISCORD_OAUTH_REDIRECT_URI="https://app.example.test/console/api/discord/oauth/callback/",
    PUBLIC_SITE_URL="https://app.example.test",
    CELERY_TASK_ALWAYS_EAGER=True,
)
class NativeDiscordBotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="discord-owner",
            email="discord-owner@example.test",
            password="pw",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Discord Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Discord Agent",
            charter="Handle Discord messages.",
            browser_use_agent=cls.browser_agent,
        )

    def _guild(self, guild_id="100", name="Guild"):
        return PersistentAgentDiscordGuild.objects.create(
            guild_id=guild_id,
            name=name,
            owner_user=self.user,
            claimed_by=self.user,
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_oauth_callback_claims_manageable_guilds_for_agent_owner(self, post_mock, get_mock):
        auth_url = start_discord_oauth(self.agent, self.user)
        self.assertIn("client_id=discord-client", auth_url)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        post_mock.return_value = _response({"access_token": "oauth-token"})
        get_mock.return_value = _response(
            [
                {"id": "100", "name": "Claimed", "icon": "abc", "permissions": str(0x20)},
                {"id": "200", "name": "Ignored", "icon": None, "permissions": "0"},
            ]
        )

        result = handle_discord_oauth_callback(state=session.state, code="code-1")

        self.assertEqual(result.claimed_count, 1)
        claim = PersistentAgentDiscordGuild.objects.get(guild_id="100")
        self.assertEqual(claim.owner_user, self.user)
        self.assertEqual(claim.name, "Claimed")
        self.assertFalse(PersistentAgentDiscordGuild.objects.filter(guild_id="200").exists())

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_oauth_callback_view_claims_guild_without_nullable_for_update_join(self, post_mock, get_mock):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        post_mock.return_value = _response({"access_token": "oauth-token"})
        get_mock.return_value = _response(
            [{"id": "100", "name": "Claimed", "icon": "abc", "permissions": str(0x20)}]
        )

        response = self.client.get(
            reverse("discord_oauth_callback"),
            {"state": session.state, "code": "code-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("gobii:discord_oauth_complete", response.content.decode())
        self.assertIn("window.close()", response.content.decode())
        self.assertTrue(PersistentAgentDiscordGuild.objects.filter(guild_id="100").exists())

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_discover_channels_filters_to_claimed_guilds_visible_to_bot(self, get_mock):
        self._guild(guild_id="100", name="Claimed")
        PersistentAgentDiscordGuild.objects.create(
            guild_id="999",
            name="Other",
            owner_user=get_user_model().objects.create_user(username="other"),
        )
        get_mock.return_value = _response(
            [
                {"id": "10", "name": "general", "type": 0},
                {"id": "11", "name": "voice", "type": 2},
                {"id": "12", "name": "updates", "type": 5},
            ]
        )

        result = discover_channels(self.agent, query="up")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["channels"], [
            {
                "guild_id": "100",
                "guild_name": "Claimed",
                "channel_id": "12",
                "channel_name": "updates",
                "label": "Claimed / #updates",
            }
        ])
        get_mock.assert_called_once()

    @tag("batch_agent_webhooks")
    def test_subscription_uniqueness_allows_only_one_active_agent_per_channel(self):
        guild = self._guild()
        result = ensure_subscription(self.agent, guild_id=guild.guild_id, channel_id="10", channel_name="general")
        self.assertTrue(result["created"])

        second_user = get_user_model().objects.create_user(username="second-discord-owner")
        second_browser = BrowserUseAgent.objects.create(user=second_user, name="Second Browser")
        second_agent = PersistentAgent.objects.create(
            user=second_user,
            name="Second Agent",
            charter="Other",
            browser_use_agent=second_browser,
        )
        with self.assertRaises(IntegrityError):
            PersistentAgentDiscordChannelSubscription.objects.create(
                agent=second_agent,
                guild=guild,
                channel_id="10",
            )

    @tag("batch_agent_webhooks")
    @patch("api.agent.comms.message_service.requests.head")
    @patch("api.agent.comms.message_service.requests.get")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_message_persists_text_and_attachments(self, schedule_mock, get_mock, head_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        get_mock.return_value = _response(content=b"hello file")
        head_mock.return_value = _response()
        message = DiscordGatewayMessage(
            message_id="500",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="300",
            author_name="Human",
            content="see attached",
            attachments=[
                {"id": "a1", "url": "https://cdn.discordapp.test/file.txt", "filename": "file.txt", "content_type": "text/plain"}
            ],
            embeds=[],
        )

        result = ingest_gateway_message(message)

        self.assertFalse(result["ignored"])
        stored = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(stored.owner_agent, self.agent)
        self.assertEqual(stored.body, "see attached")
        self.assertEqual(stored.raw_payload["source"], "discord_bot")
        self.assertEqual(stored.raw_payload["discord_message_id"], "500")
        self.assertEqual(PersistentAgentMessageAttachment.objects.filter(message=stored).count(), 1)
        self.assertEqual(stored.conversation.channel, CommsChannel.DISCORD)
        schedule_mock.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    def test_inbound_gateway_ignores_bot_and_webhook_echo_messages(self):
        bot_message = DiscordGatewayMessage(
            message_id="501",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="bot",
            author_name="Bot",
            content="ignore",
            attachments=[],
            embeds=[],
            author_is_bot=True,
        )
        webhook_message = DiscordGatewayMessage(
            message_id="502",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="webhook",
            author_name="Webhook",
            content="ignore",
            attachments=[],
            embeds=[],
            webhook_id="wh",
        )

        self.assertEqual(ingest_gateway_message(bot_message)["reason"], "bot_or_webhook")
        self.assertEqual(ingest_gateway_message(webhook_message)["reason"], "bot_or_webhook")
        self.assertFalse(PersistentAgentMessage.objects.exists())

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.requests.post")
    def test_webhook_outbound_send_uses_agent_identity_and_persists_metadata(self, post_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        post_mock.side_effect = [
            _response({"id": "wh1", "token": "token1", "name": "Gobii"}),
            _response({"id": "discord-message-1", "channel_id": "10"}),
        ]

        message = send_channel_message(self.agent, channel_id="10", body="hello discord")

        self.assertEqual(message.body, "hello discord")
        self.assertEqual(message.raw_payload["source"], "discord_bot_webhook")
        self.assertEqual(message.raw_payload["discord_message_id"], "discord-message-1")
        webhook = PersistentAgentDiscordWebhook.objects.get(channel_id="10")
        self.assertEqual(webhook.webhook_id, "wh1")
        send_call = post_mock.call_args_list[1]
        self.assertEqual(send_call.kwargs["json"]["username"], "Discord Agent")
        self.assertEqual(send_call.kwargs["json"]["content"], "hello discord")
        self.assertEqual(send_call.kwargs["params"], {"wait": "true"})

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.broadcast_message_attachment_update")
    @patch("api.services.discord_bot.requests.post")
    def test_send_message_tool_uploads_filespace_attachments(self, post_mock, broadcast_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        write_result = write_bytes_to_dir(
            self.agent,
            b"hello file",
            "/exports/report.txt",
            "text/plain",
            overwrite=True,
        )
        self.assertEqual(write_result["status"], "ok")
        post_mock.side_effect = [
            _response({"id": "wh1", "token": "token1", "name": "Gobii"}),
            _response(
                {
                    "id": "discord-message-1",
                    "channel_id": "10",
                    "attachments": [{"id": "attachment-1", "filename": "report.txt"}],
                }
            ),
        ]

        result = execute_discord_send_message(
            self.agent,
            {
                "channel_id": "10",
                "attachments": ["$[/exports/report.txt]"],
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attachment_count"], 1)
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.body, "")
        self.assertEqual(message.raw_payload["discord_sent_attachments"][0]["path"], "/exports/report.txt")
        stored_attachment = PersistentAgentMessageAttachment.objects.get(message=message)
        self.assertEqual(stored_attachment.filename, "report.txt")
        self.assertEqual(stored_attachment.content_type, "text/plain")
        self.assertEqual(stored_attachment.file_size, len(b"hello file"))
        send_call = post_mock.call_args_list[1]
        payload = json.loads(send_call.kwargs["data"]["payload_json"])
        self.assertEqual(payload["username"], "Discord Agent")
        self.assertEqual(payload["content"], "")
        self.assertEqual(send_call.kwargs["files"][0][0], "files[0]")
        self.assertEqual(send_call.kwargs["files"][0][1][0], "report.txt")
        self.assertEqual(send_call.kwargs["files"][0][1][2], "text/plain")
        self.assertNotIn("json", send_call.kwargs)
        broadcast_mock.assert_called_once_with(str(message.id))

    @tag("batch_agent_webhooks")
    def test_subscription_tool_returns_action_required_connect_url_without_claimed_guild(self):
        result = execute_discord_channel_subscriptions(
            self.agent,
            {"action": "discover_channels", "will_continue_work": False},
        )

        self.assertEqual(result["status"], "action_required")
        self.assertIn("/console/api/discord/oauth/start/", result["connect_url"])
        self.assertEqual(
            result["bot_invite_url"],
            "https://discord.com/oauth2/authorize?client_id=discord-client&scope=bot+applications.commands&permissions=536939520",
        )
        self.assertTrue(result["auto_sleep_ok"])

    @tag("batch_agent_webhooks")
    def test_connected_app_system_skill_prefers_native_discord_tools(self):
        skill = get_system_skill_definition("connected_app_channels")

        self.assertIn("discord_channel_subscriptions", skill.tool_names)
        self.assertIn("discord_send_message", skill.tool_names)
        self.assertNotIn("pipedream_trigger_subscriptions", skill.tool_names)
        self.assertIn("Use the native Gobii Discord bot tools", skill.prompt_instructions)
        self.assertIn("Discord bot invite URL", skill.prompt_instructions)
        self.assertIn("attachments", skill.prompt_instructions)

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_discover_channels_returns_bot_invite_url_when_bot_cannot_list_channels(self, get_mock):
        self._guild()
        response = _response({"message": "Missing Access"}, status_code=403)
        response.raise_for_status.side_effect = requests.HTTPError("403")
        get_mock.return_value = response

        result = discover_channels(self.agent)

        self.assertEqual(result["status"], "action_required")
        self.assertIn("cannot list channels", result["message"])
        self.assertEqual(
            result["bot_invite_url"],
            "https://discord.com/oauth2/authorize?client_id=discord-client&scope=bot+applications.commands&permissions=536939520",
        )
