import json
import os
import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from django.contrib.auth import get_user_model
from django.db import OperationalError, connection
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.agent.system_skills.registry import get_system_skill_definition
from api.agent.files.attachment_helpers import ResolvedAttachment
from api.agent.tools.discord_channel_subscriptions import execute_discord_channel_subscriptions
from api.agent.tools.send_discord_message import execute_send_discord_message
from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentDiscordChannelSubscription,
    PersistentAgentDiscordGuild,
    PersistentAgentDiscordOAuthSession,
    PersistentAgentDiscordWebhook,
    PersistentAgentDiscordWebhookEcho,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemStep,
)
from api.services.discord_bot import (
    DiscordBotIntegrationError,
    DiscordGatewayMessage,
    discover_channels,
    ensure_subscription,
    handle_discord_oauth_callback,
    ingest_gateway_message,
    send_channel_message,
    start_discord_oauth,
    _webhook_echo_signature,
)
from api.management.commands.run_discord_bot import build_gateway_message, ingest_gateway_message_with_reconnect


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

    def _force_login_console_manager(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)

    @tag("batch_agent_webhooks")
    @patch("api.management.commands.run_discord_bot.close_old_connections")
    @patch("api.management.commands.run_discord_bot.ingest_gateway_message")
    def test_run_discord_bot_retries_once_after_stale_database_connection(self, ingest_mock, close_mock):
        message = DiscordGatewayMessage(
            message_id="message-1",
            channel_id="channel-1",
            channel_name="general",
            guild_id="guild-1",
            guild_name="Guild",
            author_id="author-1",
            author_name="Author",
            content="hello",
            raw_content="hello",
            attachments=[],
            embeds=[],
        )
        ingest_mock.side_effect = [
            OperationalError("the connection is closed"),
            {"ignored": False, "message_id": "stored-message"},
        ]

        result = ingest_gateway_message_with_reconnect(message)

        self.assertEqual(result["message_id"], "stored-message")
        self.assertEqual(ingest_mock.call_count, 2)
        self.assertEqual(close_mock.call_count, 3)

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_oauth_callback_claims_manageable_guilds_for_agent_owner(self, post_mock, get_mock, delay_mock):
        auth_url = start_discord_oauth(self.agent, self.user)
        self.assertIn("client_id=discord-client", auth_url)
        auth_query = parse_qs(urlsplit(auth_url).query)
        self.assertEqual(
            auth_query["scope"],
            ["identify guilds bot applications.commands"],
        )
        self.assertEqual(auth_query["permissions"], ["536939520"])
        self.assertEqual(auth_query["response_type"], ["code"])
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        post_mock.return_value = _response({"access_token": "oauth-token"})
        get_mock.return_value = _response(
            [
                {"id": "100", "name": "Claimed", "icon": "abc", "permissions": str(0x20)},
                {"id": "200", "name": "Ignored", "icon": None, "permissions": "0"},
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = handle_discord_oauth_callback(
                state=session.state,
                code="code-1",
                selected_guild_id="100",
                selected_permissions="536939520",
            )

        self.assertEqual(result.claimed_count, 1)
        self.assertEqual(result.selected_guild_id, "100")
        self.assertEqual(result.selected_guild, {"id": "100", "name": "Claimed", "icon_hash": "abc"})
        session.refresh_from_db()
        self.assertEqual(session.selected_guild_id, "100")
        self.assertEqual(session.selected_permissions, "536939520")
        claim = PersistentAgentDiscordGuild.objects.get(guild_id="100")
        self.assertEqual(claim.owner_user, self.user)
        self.assertEqual(claim.name, "Claimed")
        self.assertFalse(PersistentAgentDiscordGuild.objects.filter(guild_id="200").exists())
        system_step = PersistentAgentSystemStep.objects.get(
            step__agent=self.agent,
            code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
        )
        self.assertIn("Discord connection completed", system_step.step.description)
        self.assertIn("discover_channels", system_step.step.description)
        self.assertIn('"selected_guild_id":"100"', system_step.notes)
        delay_mock.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.services.discord_bot._fetch_oauth_guilds")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_callback_performs_discord_requests_outside_db_transaction(
        self,
        exchange_mock,
        fetch_guilds_mock,
        delay_mock,
    ):
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        baseline_atomic_depth = len(connection.atomic_blocks)

        def exchange_code(_code):
            self.assertEqual(len(connection.atomic_blocks), baseline_atomic_depth)
            return "oauth-token"

        def fetch_guilds(_access_token):
            self.assertEqual(len(connection.atomic_blocks), baseline_atomic_depth)
            return [{"id": "100", "name": "Claimed", "icon": "abc", "permissions": str(0x20)}]

        exchange_mock.side_effect = exchange_code
        fetch_guilds_mock.side_effect = fetch_guilds

        with self.captureOnCommitCallbacks(execute=True):
            result = handle_discord_oauth_callback(state=session.state, code="code-1")

        self.assertEqual(result.claimed_count, 1)
        exchange_mock.assert_called_once_with("code-1")
        fetch_guilds_mock.assert_called_once_with("oauth-token")
        delay_mock.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_oauth_callback_view_claims_guild_without_nullable_for_update_join(self, post_mock, get_mock, delay_mock):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        post_mock.return_value = _response({"access_token": "oauth-token"})
        get_mock.return_value = _response(
            [{"id": "100", "name": "Claimed", "icon": "abc", "permissions": str(0x20)}]
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(
                reverse("discord_oauth_callback"),
                {"state": session.state, "code": "code-1", "guild_id": "100", "permissions": "536939520"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("gobii:discord_oauth_complete", response.content.decode())
        self.assertIn("window.close()", response.content.decode())
        session.refresh_from_db()
        self.assertEqual(session.selected_guild_id, "100")
        self.assertTrue(PersistentAgentDiscordGuild.objects.filter(guild_id="100").exists())
        delay_mock.assert_called_once_with(str(self.agent.id))

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
    @patch("api.services.discord_bot.requests.get")
    def test_discover_channels_defaults_to_recent_oauth_selected_guild(self, get_mock):
        self._guild(guild_id="100", name="Other")
        self._guild(guild_id="200", name="Selected")
        PersistentAgentDiscordOAuthSession.objects.create(
            state="selected-state",
            agent=self.agent,
            owner_user=self.user,
            initiated_by=self.user,
            expires_at=timezone.now() + timedelta(minutes=15),
            completed_at=timezone.now(),
            selected_guild_id="200",
        )
        get_mock.return_value = _response([{"id": "20", "name": "general", "type": 0}])

        result = discover_channels(self.agent)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["selected_guild"]["guild_id"], "200")
        self.assertEqual(result["channels"][0]["guild_id"], "200")
        self.assertEqual(result["channels"][0]["guild_name"], "Selected")
        self.assertIn("/guilds/200/channels", get_mock.call_args.args[0])

    @tag("batch_agent_webhooks")
    def test_subscription_tool_surfaces_recent_oauth_selected_guild(self):
        self._guild(guild_id="100", name="Other")
        self._guild(guild_id="200", name="Selected")
        PersistentAgentDiscordOAuthSession.objects.create(
            state="selected-state",
            agent=self.agent,
            owner_user=self.user,
            initiated_by=self.user,
            expires_at=timezone.now() + timedelta(minutes=15),
            completed_at=timezone.now(),
            selected_guild_id="200",
        )

        result = execute_discord_channel_subscriptions(
            self.agent,
            {"action": "list_guilds", "will_continue_work": True},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["selected_guild"]["guild_id"], "200")
        self.assertIn("Do not ask the user to choose a server again", result["message"])

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_subscription_uniqueness_allows_multiple_agents_per_channel(self, get_mock):
        get_mock.return_value = _response([{"id": "10", "name": "general", "type": 0}])
        guild = self._guild()
        result = ensure_subscription(self.agent, guild_id=guild.guild_id, channel_id="10", channel_name="general")
        self.assertTrue(result["created"])

        second_browser = BrowserUseAgent.objects.create(user=self.user, name="Second Browser")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Second Agent",
            charter="Other",
            browser_use_agent=second_browser,
        )
        second_result = ensure_subscription(second_agent, guild_id=guild.guild_id, channel_id="10", channel_name="general")

        self.assertTrue(second_result["created"])
        self.assertNotEqual(result["subscription"]["id"], second_result["subscription"]["id"])
        self.assertEqual(
            PersistentAgentDiscordChannelSubscription.objects.filter(
                guild=guild,
                channel_id="10",
                status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
            ).count(),
            2,
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_subscription_uniqueness_reuses_same_agent_channel(self, get_mock):
        get_mock.return_value = _response([{"id": "10", "name": "general", "type": 0}])
        guild = self._guild()
        result = ensure_subscription(self.agent, guild_id=guild.guild_id, channel_id="10", channel_name="general")

        reused = ensure_subscription(self.agent, guild_id=guild.guild_id, channel_id="10", channel_name="general-renamed")

        self.assertTrue(reused["reused"])
        self.assertEqual(result["subscription"]["id"], reused["subscription"]["id"])
        self.assertEqual(
            PersistentAgentDiscordChannelSubscription.objects.filter(
                agent=self.agent,
                guild=guild,
                channel_id="10",
                status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
            ).count(),
            1,
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_ensure_subscription_rejects_channel_outside_claimed_guild(self, get_mock):
        get_mock.return_value = _response([{"id": "99", "name": "other", "type": 0}])
        guild = self._guild()

        with self.assertRaisesRegex(DiscordBotIntegrationError, "not found in the selected server"):
            ensure_subscription(self.agent, guild_id=guild.guild_id, channel_id="10", channel_name="general")

        self.assertFalse(PersistentAgentDiscordChannelSubscription.objects.exists())

    @tag("batch_agent_webhooks")
    def test_gateway_message_builder_uses_discord_clean_content_for_mentions(self):
        message = SimpleNamespace(
            id=500,
            channel=SimpleNamespace(id=10, name="general"),
            guild=SimpleNamespace(id=100, name="Guild"),
            author=SimpleNamespace(id=300, display_name="Human", name="human", bot=False),
            content="please help <@123456789012345678>",
            clean_content="please help @Ada",
            attachments=None,
            embeds=None,
            webhook_id=None,
        )

        gateway_message = build_gateway_message(message)

        self.assertEqual(gateway_message.content, "please help @Ada")
        self.assertEqual(gateway_message.raw_content, "please help <@123456789012345678>")
        self.assertEqual(gateway_message.attachments, [])
        self.assertEqual(gateway_message.embeds, [])

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_message_persists_clean_body_and_raw_discord_content(self, schedule_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        message = DiscordGatewayMessage(
            message_id="500",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="300",
            author_name="Human",
            content="please help @Ada",
            raw_content="please help <@123456789012345678>",
            attachments=[],
            embeds=[],
        )

        result = ingest_gateway_message(message)

        self.assertFalse(result["ignored"])
        stored = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(stored.body, "please help @Ada")
        self.assertEqual(stored.raw_payload["discord_content"], "please help @Ada")
        self.assertEqual(stored.raw_payload["discord_raw_content"], "please help <@123456789012345678>")

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
        schedule_mock.assert_called_once_with(str(self.agent.id), typing_channel_id="10")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_message_fans_out_to_all_active_channel_agents(self, schedule_mock):
        guild = self._guild()
        second_browser = BrowserUseAgent.objects.create(user=self.user, name="Second Browser")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Second Agent",
            charter="Also handle Discord messages.",
            browser_use_agent=second_browser,
        )
        first_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        second_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=second_agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        message = DiscordGatewayMessage(
            message_id="500",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="300",
            author_name="Human",
            content="hello both agents",
            attachments=[],
            embeds=[],
        )

        result = ingest_gateway_message(message)

        self.assertFalse(result["ignored"])
        self.assertEqual(result["subscription_count"], 2)
        self.assertEqual(len(result["deliveries"]), 2)
        self.assertCountEqual(
            [delivery["subscription_id"] for delivery in result["deliveries"]],
            [str(first_subscription.id), str(second_subscription.id)],
        )
        stored_messages = PersistentAgentMessage.objects.order_by("owner_agent_id")
        self.assertEqual(stored_messages.count(), 2)
        self.assertCountEqual(
            [str(stored.owner_agent_id) for stored in stored_messages],
            [str(self.agent.id), str(second_agent.id)],
        )
        self.assertCountEqual(
            [call.args[0] for call in schedule_mock.call_args_list],
            [str(self.agent.id), str(second_agent.id)],
        )

    @tag("batch_agent_webhooks")
    @override_settings(GOBII_RELEASE_ENV="local")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_message_skips_agents_from_other_environment(self, schedule_mock):
        self.agent.execution_environment = "local"
        self.agent.save(update_fields=["execution_environment", "updated_at"])
        guild = self._guild()
        foreign_browser = BrowserUseAgent.objects.create(user=self.user, name="Staging Browser")
        foreign_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Staging Agent",
            charter="Handle Discord messages somewhere else.",
            browser_use_agent=foreign_browser,
            execution_environment="staging",
        )
        active_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=foreign_agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        message = DiscordGatewayMessage(
            message_id="500",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="300",
            author_name="Human",
            content="hello local agents",
            attachments=[],
            embeds=[],
        )

        result = ingest_gateway_message(message)

        self.assertFalse(result["ignored"])
        self.assertEqual(result["subscription_count"], 1)
        self.assertEqual(result["deliveries"][0]["subscription_id"], str(active_subscription.id))
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=self.agent).count(), 1)
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=foreign_agent).exists())
        schedule_mock.assert_called_once_with(str(self.agent.id), typing_channel_id="10")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_ignores_bot_messages_but_ingests_third_party_webhooks(self, schedule_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
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
            author_is_bot=True,
            webhook_id="wh",
        )

        self.assertEqual(ingest_gateway_message(bot_message)["reason"], "bot")
        webhook_result = ingest_gateway_message(webhook_message)
        self.assertFalse(webhook_result["ignored"])
        self.assertEqual(PersistentAgentMessage.objects.count(), 1)
        stored = PersistentAgentMessage.objects.get()
        self.assertEqual(stored.raw_payload["discord_webhook_id"], "wh")
        schedule_mock.assert_called_once_with(str(self.agent.id), typing_channel_id="10")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_filters_only_the_sending_agents_webhook_echo(self, schedule_mock):
        guild = self._guild()
        second_browser = BrowserUseAgent.objects.create(user=self.user, name="Second Browser")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Second Agent",
            charter="Also handle Discord messages.",
            browser_use_agent=second_browser,
        )
        first_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        second_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=second_agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        webhook = PersistentAgentDiscordWebhook.objects.create(
            guild=guild,
            channel_id="10",
            webhook_id="wh",
            name="Gobii",
        )
        PersistentAgentDiscordWebhookEcho.objects.create(
            agent=self.agent,
            webhook=webhook,
            channel_id="10",
            discord_webhook_id="wh",
            signature_hash=_webhook_echo_signature(
                webhook_id="wh",
                channel_id="10",
                username="Discord Agent",
                body="hello from agent one",
                attachment_filenames=[],
            ),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        webhook_message = DiscordGatewayMessage(
            message_id="502",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="webhook",
            author_name="Discord Agent",
            content="hello from agent one",
            attachments=[],
            embeds=[],
            author_is_bot=True,
            webhook_id="wh",
        )

        result = ingest_gateway_message(webhook_message)

        self.assertFalse(result["ignored"])
        self.assertEqual(result["subscription_count"], 1)
        self.assertEqual(result["skipped_subscription_ids"], [str(first_subscription.id)])
        self.assertEqual(result["deliveries"][0]["subscription_id"], str(second_subscription.id))
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=False).count(), 0)
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=second_agent, is_outbound=False).count(), 1)
        schedule_mock.assert_called_once_with(str(second_agent.id), typing_channel_id="10")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_webhook_echo_matches_raw_discord_mentions(self, schedule_mock):
        guild = self._guild()
        subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        webhook = PersistentAgentDiscordWebhook.objects.create(
            guild=guild,
            channel_id="10",
            webhook_id="wh",
            name="Gobii",
        )
        raw_body = "please help <@123456789012345678>"
        PersistentAgentDiscordWebhookEcho.objects.create(
            agent=self.agent,
            webhook=webhook,
            channel_id="10",
            discord_webhook_id="wh",
            signature_hash=_webhook_echo_signature(
                webhook_id="wh",
                channel_id="10",
                username="Discord Agent",
                body=raw_body,
                attachment_filenames=[],
            ),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        webhook_message = DiscordGatewayMessage(
            message_id="503",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="webhook",
            author_name="Discord Agent",
            content="please help @Ada",
            raw_content=raw_body,
            attachments=[],
            embeds=[],
            author_is_bot=True,
            webhook_id="wh",
        )

        result = ingest_gateway_message(webhook_message)

        self.assertTrue(result["ignored"])
        self.assertEqual(result["reason"], "own_webhook_echo")
        self.assertEqual(result["skipped_subscription_ids"], [str(subscription.id)])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=False).exists())
        schedule_mock.assert_not_called()

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_webhook_outbound_send_uses_agent_identity_and_persists_metadata(self, post_mock, get_mock):
        get_mock.return_value = _response([{"id": "10", "name": "general", "type": 0}])
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        webhook = PersistentAgentDiscordWebhook.objects.create(
            guild=guild,
            channel_id="10",
            webhook_id="old-wh",
            name="Old Gobii",
        )
        expired_marker = PersistentAgentDiscordWebhookEcho.objects.create(
            agent=self.agent,
            webhook=webhook,
            channel_id="10",
            discord_webhook_id="old-wh",
            signature_hash="expired",
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        def post_side_effect(url, **_kwargs):
            if "/channels/" in url:
                return _response({"id": "wh1", "token": "token1", "name": "Gobii"})
            marker = PersistentAgentDiscordWebhookEcho.objects.get(agent=self.agent, channel_id="10")
            self.assertEqual(marker.discord_webhook_id, "wh1")
            self.assertEqual(marker.discord_message_id, "")
            self.assertEqual(marker.matched_at, None)
            return _response({"id": "discord-message-1", "channel_id": "10"})

        post_mock.side_effect = post_side_effect

        message = send_channel_message(self.agent, channel_id="10", body="hello discord")

        self.assertEqual(message.body, "hello discord")
        self.assertEqual(message.raw_payload["source"], "discord_bot_webhook")
        self.assertEqual(message.raw_payload["discord_message_id"], "discord-message-1")
        webhook = PersistentAgentDiscordWebhook.objects.get(channel_id="10")
        self.assertEqual(webhook.webhook_id, "wh1")
        marker = PersistentAgentDiscordWebhookEcho.objects.get(agent=self.agent, channel_id="10")
        self.assertNotEqual(marker.id, expired_marker.id)
        self.assertEqual(marker.discord_message_id, "discord-message-1")
        self.assertEqual(message.raw_payload["webhook_echo_marker_id"], str(marker.id))
        send_call = post_mock.call_args_list[1]
        self.assertEqual(send_call.kwargs["json"]["username"], "Discord Agent")
        self.assertEqual(send_call.kwargs["json"]["content"], "hello discord")
        self.assertEqual(send_call.kwargs["params"], {"wait": "true"})

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_webhook_outbound_send_decodes_literal_unicode_escapes(self, post_mock, get_mock):
        get_mock.return_value = _response([{"id": "10", "name": "general", "type": 0}])
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

        message = send_channel_message(
            self.agent,
            channel_id="10",
            body=r"Company \u2500 Approach \u2500 Weakness\nUse \n literally",
        )

        self.assertEqual(message.body, "Company ─ Approach ─ Weakness\\nUse \\n literally")
        send_call = post_mock.call_args_list[1]
        self.assertEqual(send_call.kwargs["json"]["content"], "Company ─ Approach ─ Weakness\\nUse \\n literally")

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.build_public_agent_avatar_thumbnail_url", return_value="https://app.example.test/public/agents/avatar.png")
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_webhook_outbound_send_uses_public_agent_avatar_thumbnail(self, post_mock, get_mock, avatar_url_mock):
        get_mock.return_value = _response([{"id": "10", "name": "general", "type": 0}])
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

        send_channel_message(self.agent, channel_id="10", body="hello discord")

        send_call = post_mock.call_args_list[1]
        self.assertEqual(send_call.kwargs["json"]["avatar_url"], "https://app.example.test/public/agents/avatar.png")
        avatar_url_mock.assert_called_once_with(self.agent)

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.broadcast_message_attachment_update")
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_send_message_tool_uploads_filespace_attachments(self, post_mock, get_mock, broadcast_mock):
        get_mock.return_value = _response([{"id": "10", "name": "general", "type": 0}])
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

        result = execute_send_discord_message(
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
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_webhook_send_rejects_subscription_channel_not_in_claimed_guild(self, post_mock, get_mock):
        get_mock.return_value = _response([{"id": "99", "name": "other", "type": 0}])
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )

        with self.assertRaisesRegex(DiscordBotIntegrationError, "not found in the selected server"):
            send_channel_message(self.agent, channel_id="10", body="hello discord")

        post_mock.assert_not_called()

    @tag("batch_agent_webhooks")
    @override_settings(DISCORD_WEBHOOK_MAX_TOTAL_ATTACHMENT_BYTES=10)
    def test_webhook_send_rejects_total_attachment_size_over_limit(self):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        attachment = ResolvedAttachment(
            node=MagicMock(),
            path="/exports/big.bin",
            filename="big.bin",
            content_type="application/octet-stream",
            size_bytes=11,
        )

        with self.assertRaisesRegex(ValueError, "configured total upload limit"):
            send_channel_message(self.agent, channel_id="10", body="", attachments=[attachment])

    @tag("batch_agent_webhooks")
    def test_subscription_tool_returns_action_required_connect_url_without_claimed_guild(self):
        result = execute_discord_channel_subscriptions(
            self.agent,
            {"action": "discover_channels", "will_continue_work": False},
        )

        self.assertEqual(result["status"], "action_required")
        self.assertIn("single setup link", result["message"])
        self.assertIn("/console/api/discord/oauth/start/", result["connect_url"])
        self.assertEqual(
            result["bot_invite_url"],
            "https://discord.com/oauth2/authorize?client_id=discord-client&scope=bot+applications.commands&permissions=536939520",
        )
        self.assertTrue(result["auto_sleep_ok"])

    @tag("batch_agent_webhooks")
    def test_subscription_tool_list_guilds_returns_action_required_connect_url_without_claimed_guild(self):
        result = execute_discord_channel_subscriptions(
            self.agent,
            {"action": "list_guilds", "will_continue_work": False},
        )

        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["guilds"], [])
        self.assertIn("single setup link", result["message"])
        self.assertIn("/console/api/discord/oauth/start/", result["connect_url"])
        self.assertTrue(result["auto_sleep_ok"])

    @tag("batch_agent_webhooks")
    def test_discord_native_system_skill_prefers_native_discord_tools(self):
        skill = get_system_skill_definition("discord_native")

        self.assertIn("discord_channel_subscriptions", skill.tool_names)
        self.assertIn("send_discord_message", skill.tool_names)
        self.assertNotIn("pipedream_trigger_subscriptions", skill.tool_names)
        self.assertIn("Use the native Gobii Discord bot tools", skill.prompt_instructions)
        self.assertIn("immediately call `discord_channel_subscriptions`", skill.prompt_instructions)
        self.assertIn("do not ask whether to start setup first", skill.prompt_instructions)
        self.assertIn("Never invent Discord setup links", skill.prompt_instructions)
        self.assertIn("single setup link", skill.prompt_instructions)
        self.assertIn("fallback repair link", skill.prompt_instructions)
        self.assertIn("To upload files", skill.prompt_instructions)
        self.assertIn("filespace paths or $[/path]", skill.prompt_instructions)
        self.assertIn("Body text never attaches files", skill.prompt_instructions)

    @tag("batch_agent_webhooks")
    def test_discord_app_api_returns_agent_state(self):
        self._force_login_console_manager()
        guild = self._guild(name="Support")
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="triage",
        )
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key="discord_native",
            is_enabled=True,
        )

        response = self.client.get(reverse("console-agent-discord-app", args=[self.agent.id]))

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["provider_key"], "discord")
        self.assertTrue(payload["connected"])
        self.assertTrue(payload["subscribed"])
        self.assertTrue(payload["skill_enabled"])
        self.assertEqual(payload["guild_count"], 1)
        self.assertEqual(payload["active_subscription_count"], 1)
        self.assertEqual(payload["guilds"][0]["name"], "Support")
        self.assertEqual(payload["subscriptions"][0]["channel_name"], "triage")
        self.assertIn("/console/api/discord/oauth/start/", payload["connect_url"])

    @tag("batch_agent_webhooks")
    def test_discord_connect_api_enables_native_skill(self):
        self._force_login_console_manager()

        response = self.client.post(reverse("console-agent-discord-connect", args=[self.agent.id]))

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["skill_enabled"])
        self.assertIn("/console/api/discord/oauth/start/", payload["connect_url"])
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key="discord_native",
                is_enabled=True,
            ).exists()
        )

    @tag("batch_agent_webhooks")
    def test_discord_disconnect_api_removes_owner_connections_and_subscriptions(self):
        self._force_login_console_manager()
        guild = self._guild(guild_id="100", name="Support")
        other_user = get_user_model().objects.create_user(
            username="other-discord-owner",
            email="other-discord-owner@example.test",
            password="pw",
        )
        other_browser = BrowserUseAgent.objects.create(user=other_user, name="Other Discord Browser")
        other_agent = PersistentAgent.objects.create(
            user=other_user,
            name="Other Discord Agent",
            charter="Handle other Discord messages.",
            browser_use_agent=other_browser,
        )
        other_guild = PersistentAgentDiscordGuild.objects.create(
            guild_id="200",
            name="Other Guild",
            owner_user=other_user,
            claimed_by=other_user,
        )
        subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="triage",
        )
        other_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=other_agent,
            guild=other_guild,
            channel_id="20",
            channel_name="other",
        )
        webhook = PersistentAgentDiscordWebhook.objects.create(
            guild=guild,
            channel_id="10",
            webhook_id="webhook-1",
            name="Gobii",
        )
        PersistentAgentDiscordWebhookEcho.objects.create(
            agent=self.agent,
            webhook=webhook,
            channel_id="10",
            discord_webhook_id="webhook-1",
            signature_hash="signature",
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        skill_state = PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key="discord_native",
            is_enabled=True,
        )
        other_skill_state = PersistentAgentSystemSkillState.objects.create(
            agent=other_agent,
            skill_key="discord_native",
            is_enabled=True,
        )

        response = self.client.post(reverse("console-discord-disconnect"))

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["revoked"])
        self.assertEqual(payload["guilds_disconnected"], 1)
        self.assertEqual(payload["subscriptions_disabled"], 1)
        guild.refresh_from_db()
        subscription.refresh_from_db()
        skill_state.refresh_from_db()
        other_guild.refresh_from_db()
        other_subscription.refresh_from_db()
        other_skill_state.refresh_from_db()
        self.assertFalse(guild.is_active)
        self.assertEqual(subscription.status, PersistentAgentDiscordChannelSubscription.Status.DISABLED)
        self.assertFalse(skill_state.is_enabled)
        self.assertFalse(PersistentAgentDiscordWebhook.objects.filter(id=webhook.id).exists())
        self.assertTrue(other_guild.is_active)
        self.assertEqual(other_subscription.status, PersistentAgentDiscordChannelSubscription.Status.ACTIVE)
        self.assertTrue(other_skill_state.is_enabled)

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_discord_channels_api_discovers_channels(self, get_mock):
        self._force_login_console_manager()
        self._guild(guild_id="100", name="Support")
        get_mock.return_value = _response(
            [
                {"id": "10", "name": "triage", "type": 0},
                {"id": "11", "name": "announcements", "type": 5},
                {"id": "12", "name": "voice", "type": 2},
            ]
        )

        response = self.client.get(reverse("console-agent-discord-channels", args=[self.agent.id, "100"]))

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual([channel["channel_name"] for channel in payload["channels"]], ["triage", "announcements"])

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_discord_subscriptions_api_replaces_active_selection_and_enables_skill(self, get_mock):
        self._force_login_console_manager()
        guild = self._guild(guild_id="100", name="Support")
        old_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="9",
            channel_name="old",
        )
        get_mock.return_value = _response(
            [
                {"id": "10", "name": "triage", "type": 0},
                {"id": "9", "name": "old", "type": 0},
            ]
        )

        response = self.client.patch(
            reverse("console-agent-discord-subscriptions", args=[self.agent.id]),
            data=json.dumps({"subscriptions": [{"guild_id": "100", "channel_id": "10", "channel_name": "triage"}]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        old_subscription.refresh_from_db()
        self.assertEqual(old_subscription.status, PersistentAgentDiscordChannelSubscription.Status.DISABLED)
        self.assertTrue(
            PersistentAgentDiscordChannelSubscription.objects.filter(
                agent=self.agent,
                guild=guild,
                channel_id="10",
                status=PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key="discord_native",
                is_enabled=True,
            ).exists()
        )

    @tag("batch_agent_webhooks")
    def test_discord_native_migration_merges_legacy_skill_rows(self):
        migration = importlib.import_module("api.migrations.0390_rename_connected_app_channels_to_discord_native")
        old_state = PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key="connected_app_channels",
            is_enabled=True,
            usage_count=2,
            last_used_at=timezone.now() - timedelta(hours=1),
        )
        new_state = PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key="discord_native",
            is_enabled=False,
            usage_count=3,
            last_used_at=timezone.now() - timedelta(hours=2),
        )

        class Apps:
            @staticmethod
            def get_model(app_label, model_name):
                self.assertEqual(app_label, "api")
                self.assertEqual(model_name, "PersistentAgentSystemSkillState")
                return PersistentAgentSystemSkillState

        migration.migrate_discord_native_skill_state(Apps(), None)

        self.assertFalse(PersistentAgentSystemSkillState.objects.filter(id=old_state.id).exists())
        new_state.refresh_from_db()
        self.assertTrue(new_state.is_enabled)
        self.assertEqual(new_state.usage_count, 5)
        self.assertGreaterEqual(new_state.last_used_at, old_state.last_used_at)

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
