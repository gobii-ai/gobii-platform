import json
import os
import importlib
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError, connection
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.agent.system_skills.registry import get_system_skill_definition
from api.agent.core.prompt_context import _format_discord_reply_context, _get_system_instruction, build_prompt_context
from api.agent.files.attachment_helpers import ResolvedAttachment
from api.agent.tools.add_discord_reaction import execute_add_discord_reaction
from api.agent.tools.discord_channel_subscriptions import (
    execute_discord_channel_subscriptions,
    get_discord_channel_subscriptions_tool,
)
from api.agent.tools.send_discord_message import execute_send_discord_message, get_send_discord_message_tool
from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentDiscordChannelSubscription,
    PersistentAgentDiscordGuild,
    PersistentAgentDiscordOAuthSession,
    PersistentAgentDiscordWebhook,
    PersistentAgentDiscordWebhookEcho,
    PersistentAgentEnabledTool,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemStep,
    UserDiscordIdentity,
)
from api.services.discord_bot import (
    add_discord_reaction,
    claimed_guild_queryset_for_owner,
    DiscordBotIntegrationError,
    DiscordGatewayMessage,
    discover_channels,
    ensure_subscription,
    handle_discord_identity_oauth_callback,
    handle_discord_oauth_callback,
    ingest_gateway_message,
    send_inactive_discord_auto_reply,
    send_channel_message,
    start_discord_oauth,
    start_discord_identity_oauth,
    _agent_webhook_username,
    _raise_for_discord_status,
    _webhook_echo_signature,
)
from api.services.discord_markdown import normalize_discord_markdown
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
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_identity_oauth_links_verified_discord_user_without_storing_token(self, post_mock, get_mock):
        auth_url = start_discord_identity_oauth(self.user)
        auth_query = parse_qs(urlsplit(auth_url).query)
        self.assertEqual(auth_query["scope"], ["identify"])
        self.assertNotIn("permissions", auth_query)
        self.assertNotIn("integration_type", auth_query)

        state = auth_query["state"][0]
        self.assertTrue(state.startswith("identity_"))
        post_mock.return_value = _response({
            "access_token": "short-lived-token",
            "scope": "identify",
        })
        get_mock.return_value = _response({
            "id": "177593384389705729",
            "username": "verified_user",
            "global_name": "Verified User",
        })

        identity = handle_discord_identity_oauth_callback(
            state=state,
            code="identity-code",
            user=self.user,
        )

        self.assertEqual(identity.discord_user_id, "177593384389705729")
        self.assertEqual(identity.username, "verified_user")
        self.assertEqual(identity.global_name, "Verified User")
        self.assertFalse(hasattr(identity, "access_token"))
        self.assertEqual(get_mock.call_args.kwargs["headers"], {"Authorization": "Bearer short-lived-token"})

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot._fetch_discord_current_user")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_identity_oauth_rejects_discord_account_linked_to_another_user(self, exchange_mock, user_mock):
        other_user = get_user_model().objects.create_user(
            username="other-discord-user",
            email="other-discord-user@example.test",
            password="pw",
        )
        UserDiscordIdentity.objects.create(
            user=other_user,
            discord_user_id="177593384389705729",
            username="already_linked",
            verified_at=timezone.now(),
        )
        auth_url = start_discord_identity_oauth(self.user)
        state = parse_qs(urlsplit(auth_url).query)["state"][0]
        exchange_mock.return_value = {"access_token": "token", "scope": "identify"}
        user_mock.return_value = {
            "id": "177593384389705729",
            "username": "already_linked",
            "global_name": "",
        }

        with self.assertRaisesMessage(DiscordBotIntegrationError, "already linked to another Gobii user"):
            handle_discord_identity_oauth_callback(
                state=state,
                code="identity-code",
                user=self.user,
            )

        self.assertFalse(UserDiscordIdentity.objects.filter(user=self.user).exists())

    @tag("batch_agent_webhooks")
    @override_settings(ALLOWED_HOSTS=["profile.example.test", "callback.example.test"])
    @patch("api.services.discord_bot._fetch_discord_current_user")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_identity_oauth_routes_complete_profile_link(self, exchange_mock, user_mock):
        self.client.force_login(self.user)
        start_response = self.client.get(
            reverse("discord_identity_oauth_start"),
            HTTP_HOST="profile.example.test",
            secure=True,
        )
        self.assertEqual(start_response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(start_response.url).query)["scope"], ["identify"])
        state = parse_qs(urlsplit(start_response.url).query)["state"][0]
        exchange_mock.return_value = {"access_token": "token", "scope": "identify"}
        user_mock.return_value = {
            "id": "177593384389705729",
            "username": "route_verified",
            "global_name": "Route Verified",
        }

        callback_response = self.client.get(
            reverse("discord_oauth_callback"),
            {"state": state, "code": "identity-code"},
            HTTP_HOST="callback.example.test",
            secure=True,
        )

        self.assertEqual(callback_response.status_code, 200)
        self.assertContains(callback_response, "gobii:discord_identity_oauth_complete")
        self.assertContains(callback_response, '"status": "success"')
        self.assertContains(callback_response, '"https://profile.example.test"')
        self.assertContains(callback_response, "window.opener.postMessage(payload, targetOrigin)")
        self.assertContains(callback_response, "localStorage.setItem(`gobii:oauth_complete:")
        identity = UserDiscordIdentity.objects.get(user=self.user)
        self.assertEqual(identity.discord_user_id, "177593384389705729")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.DISCORD_IDENTITY_OAUTH_STATE_MAX_AGE_SECONDS", -1)
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_expired_identity_oauth_session_is_rejected_before_token_exchange(self, exchange_mock):
        auth_url = start_discord_identity_oauth(self.user)
        state = parse_qs(urlsplit(auth_url).query)["state"][0]

        with self.assertRaisesMessage(DiscordBotIntegrationError, "has expired"):
            handle_discord_identity_oauth_callback(
                state=state,
                code="identity-code",
                user=self.user,
            )

        exchange_mock.assert_not_called()

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_identity_oauth_state_is_bound_to_gobii_user(self, exchange_mock):
        auth_url = start_discord_identity_oauth(self.user)
        state = parse_qs(urlsplit(auth_url).query)["state"][0]
        other_user = get_user_model().objects.create_user(username="discord-state-other")

        with self.assertRaisesMessage(DiscordBotIntegrationError, "not created for this user"):
            handle_discord_identity_oauth_callback(
                state=state,
                code="identity-code",
                user=other_user,
            )

        exchange_mock.assert_not_called()

    @tag("batch_agent_webhooks")
    def test_discord_skill_prompt_lists_active_server_and_channel_names_and_ids(self):
        guild = self._guild(guild_id="100", name="Product Team")
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="200",
            channel_name="agent-ops",
        )
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="201",
            channel_name="disabled-channel",
            status=PersistentAgentDiscordChannelSubscription.Status.DISABLED,
        )

        context = get_system_skill_definition("discord_native").render_prompt_context(self.agent)

        self.assertIn("Product Team (guild_id=100)", context)
        self.assertIn("#agent-ops (channel_id=200)", context)
        self.assertNotIn("disabled-channel", context)

    @tag("batch_agent_webhooks")
    def test_discord_http_error_preserves_escaped_response_body(self):
        response = _response(status_code=400)
        response.text = (
            '{"message":"Invalid Form Body","errors":{"content":{"_errors":'
            '[{"code":"BASE_TYPE_MAX_LENGTH","message":"Must be 2000 or fewer in length."}]}}}'
        )
        response.raise_for_status.side_effect = requests.HTTPError("400")

        with self.assertRaises(DiscordBotIntegrationError) as raised:
            _raise_for_discord_status(response, action="webhook send")

        message = str(raised.exception)
        self.assertIn('"Invalid Form Body"', message)
        self.assertIn('"BASE_TYPE_MAX_LENGTH"', message)
        self.assertIn('"Must be 2000 or fewer in length."', message)

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
    def test_oauth_callback_claims_only_installed_guild_for_agent_owner(self, post_mock, get_mock, delay_mock):
        auth_url = start_discord_oauth(self.agent, self.user)
        self.assertIn("client_id=discord-client", auth_url)
        auth_query = parse_qs(urlsplit(auth_url).query)
        self.assertEqual(
            auth_query["scope"],
            ["bot applications.commands"],
        )
        self.assertEqual(auth_query["permissions"], ["536939584"])
        self.assertEqual(auth_query["response_type"], ["code"])
        self.assertEqual(auth_query["integration_type"], ["0"])
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        post_mock.return_value = _response({
            "access_token": "oauth-token",
            "scope": "bot applications.commands",
            "guild": {"id": "100", "name": "Claimed", "icon": "abc"},
        })
        get_mock.return_value = _response({"id": "100", "name": "Claimed", "icon": "abc"})

        with self.captureOnCommitCallbacks(execute=True):
            result = handle_discord_oauth_callback(
                state=session.state,
                code="code-1",
            )

        self.assertEqual(
            result,
            {"guild_id": "100", "name": "Claimed", "icon_hash": "abc"},
        )
        session.refresh_from_db()
        self.assertEqual(session.selected_guild_id, "100")
        self.assertEqual(session.selected_permissions, "")
        claim = PersistentAgentDiscordGuild.objects.get(guild_id="100")
        self.assertEqual(claim.owner_user, self.user)
        self.assertEqual(claim.name, "Claimed")
        self.assertEqual(
            claim.authorization_source,
            PersistentAgentDiscordGuild.AuthorizationSource.EXPLICIT_OAUTH,
        )
        self.assertFalse(PersistentAgentDiscordGuild.objects.filter(guild_id="200").exists())
        self.assertNotIn("/users/@me/guilds", get_mock.call_args.args[0])
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
    @patch("api.services.discord_bot._fetch_bot_guild")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_callback_performs_discord_requests_outside_db_transaction(
        self,
        exchange_mock,
        fetch_bot_guild_mock,
        delay_mock,
    ):
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        baseline_atomic_depth = len(connection.atomic_blocks)

        def exchange_code(_code):
            self.assertEqual(len(connection.atomic_blocks), baseline_atomic_depth)
            return {
                "access_token": "oauth-token",
                "guild": {"id": "100", "name": "Claimed", "icon": "abc"},
            }

        def fetch_bot_guild(_guild_id):
            self.assertEqual(len(connection.atomic_blocks), baseline_atomic_depth)
            return {"id": "100", "name": "Claimed", "icon": "abc"}

        exchange_mock.side_effect = exchange_code
        fetch_bot_guild_mock.side_effect = fetch_bot_guild

        with self.captureOnCommitCallbacks(execute=True):
            result = handle_discord_oauth_callback(state=session.state, code="code-1")

        self.assertEqual(result["guild_id"], "100")
        exchange_mock.assert_called_once_with("code-1")
        fetch_bot_guild_mock.assert_called_once_with("100")
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
        post_mock.return_value = _response({
            "access_token": "oauth-token",
            "guild": {"id": "100", "name": "Claimed", "icon": "abc"},
        })
        get_mock.return_value = _response({"id": "100", "name": "Claimed", "icon": "abc"})

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
    def test_oauth_repair_url_locks_discord_picker_to_requested_guild(self):
        self._guild(guild_id="100", name="Claimed")

        auth_url = start_discord_oauth(
            self.agent,
            self.user,
            requested_guild_id="100",
        )

        auth_query = parse_qs(urlsplit(auth_url).query)
        self.assertEqual(auth_query["guild_id"], ["100"])
        self.assertEqual(auth_query["disable_guild_select"], ["true"])
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        self.assertEqual(session.requested_guild_id, "100")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_callback_requires_authoritative_token_guild(self, exchange_mock):
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        exchange_mock.side_effect = DiscordBotIntegrationError(
            "Discord OAuth did not identify the installed server."
        )

        with self.assertRaisesRegex(DiscordBotIntegrationError, "did not identify"):
            handle_discord_oauth_callback(state=session.state, code="code-1")

        self.assertFalse(PersistentAgentDiscordGuild.objects.exists())

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot._fetch_bot_guild")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_callback_does_not_claim_unverified_bot_guild(self, exchange_mock, fetch_bot_guild_mock):
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        exchange_mock.return_value = {
            "access_token": "oauth-token",
            "guild": {"id": "100", "name": "Claimed"},
        }
        fetch_bot_guild_mock.side_effect = DiscordBotIntegrationError(
            "Discord installed server verification failed."
        )

        with self.assertRaisesRegex(DiscordBotIntegrationError, "verification failed"):
            handle_discord_oauth_callback(state=session.state, code="code-1")

        self.assertFalse(PersistentAgentDiscordGuild.objects.exists())

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot._fetch_bot_guild")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_repair_rejects_different_authoritative_guild(self, exchange_mock, fetch_bot_guild_mock):
        self._guild(guild_id="100", name="Claimed")
        start_discord_oauth(self.agent, self.user, requested_guild_id="100")
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        exchange_mock.return_value = {
            "access_token": "oauth-token",
            "guild": {"id": "200", "name": "Different"},
        }
        fetch_bot_guild_mock.return_value = {"id": "200", "name": "Different"}

        with self.assertRaisesRegex(DiscordBotIntegrationError, "different server"):
            handle_discord_oauth_callback(state=session.state, code="code-1")

        self.assertFalse(PersistentAgentDiscordGuild.objects.filter(guild_id="200").exists())

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.services.discord_bot._fetch_bot_guild")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_can_add_multiple_explicit_guilds_to_same_context(
        self,
        exchange_mock,
        fetch_bot_guild_mock,
        _delay_mock,
    ):
        for guild_id in ("100", "200"):
            start_discord_oauth(self.agent, self.user)
            session = PersistentAgentDiscordOAuthSession.objects.filter(agent=self.agent).latest("created_at")
            exchange_mock.return_value = {
                "access_token": f"oauth-{guild_id}",
                "guild": {"id": guild_id, "name": f"Guild {guild_id}"},
            }
            fetch_bot_guild_mock.return_value = {"id": guild_id, "name": f"Guild {guild_id}"}
            with self.captureOnCommitCallbacks(execute=True):
                handle_discord_oauth_callback(state=session.state, code=f"code-{guild_id}")

        self.assertEqual(
            list(
                PersistentAgentDiscordGuild.objects.order_by("guild_id").values_list(
                    "guild_id",
                    flat=True,
                )
            ),
            ["100", "200"],
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot._fetch_bot_guild")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_rejects_legacy_guild_claimed_by_another_context(self, exchange_mock, fetch_bot_guild_mock):
        other_user = get_user_model().objects.create_user(username="other-claim-owner")
        PersistentAgentDiscordGuild.objects.create(
            guild_id="100",
            name="Other Context",
            owner_user=other_user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        exchange_mock.return_value = {
            "access_token": "oauth-token",
            "guild": {"id": "100", "name": "Claimed"},
        }
        fetch_bot_guild_mock.return_value = {"id": "100", "name": "Claimed"}

        with self.assertRaisesRegex(DiscordBotIntegrationError, "another Gobii context"):
            handle_discord_oauth_callback(state=session.state, code="code-1")

        self.assertEqual(
            PersistentAgentDiscordGuild.objects.get(guild_id="100").owner_user,
            other_user,
        )

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.services.discord_bot._fetch_bot_guild")
    @patch("api.services.discord_bot._exchange_oauth_code")
    def test_oauth_reauthorizes_existing_same_context_guild(
        self,
        exchange_mock,
        fetch_bot_guild_mock,
        _delay_mock,
    ):
        existing = self._guild(guild_id="100", name="Old Name")
        start_discord_oauth(self.agent, self.user)
        session = PersistentAgentDiscordOAuthSession.objects.get(agent=self.agent)
        exchange_mock.return_value = {
            "access_token": "oauth-token",
            "guild": {"id": "100", "name": "New Name", "icon": "new-icon"},
        }
        fetch_bot_guild_mock.return_value = {
            "id": "100",
            "name": "New Name",
            "icon": "new-icon",
        }

        with self.captureOnCommitCallbacks(execute=True):
            result = handle_discord_oauth_callback(state=session.state, code="code-1")

        existing.refresh_from_db()
        self.assertEqual(result["guild_id"], "100")
        self.assertEqual(PersistentAgentDiscordGuild.objects.filter(guild_id="100").count(), 1)
        self.assertEqual(existing.name, "New Name")
        self.assertEqual(existing.icon_hash, "new-icon")

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
    def test_subscription_tool_ensures_exact_channel_name_within_claimed_guild(self, get_mock):
        get_mock.return_value = _response(
            [
                {"id": "10", "name": "general", "type": 0},
                {"id": "11", "name": "releases", "type": 0},
            ]
        )
        guild = self._guild()

        result = execute_discord_channel_subscriptions(
            self.agent,
            {
                "action": "ensure",
                "guild_id": guild.guild_id,
                "channel_name": "#Releases",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["subscription"]["channel_id"], "11")
        self.assertEqual(result["subscription"]["channel_name"], "releases")
        self.assertTrue(result["auto_sleep_ok"])

    @tag("batch_agent_webhooks")
    def test_discord_tool_contracts_accept_human_channel_names(self):
        subscription_parameters = get_discord_channel_subscriptions_tool()["function"]["parameters"]
        send_parameters = get_send_discord_message_tool()["function"]["parameters"]

        self.assertIn("channel_name", subscription_parameters["properties"])
        self.assertNotIn("channel_id", subscription_parameters["required"])
        self.assertIn("channel_name", send_parameters["properties"])
        self.assertNotIn("channel_id", send_parameters["required"])

    @tag("batch_agent_webhooks")
    def test_claimed_guild_queryset_is_lockable_without_distinct(self):
        self._guild()

        queryset = claimed_guild_queryset_for_owner(owner_user=self.user).select_for_update()

        self.assertFalse(queryset.query.distinct)
        self.assertEqual(list(queryset.values_list("guild_id", flat=True)), ["100"])

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
    def test_gateway_message_builder_captures_resolved_reply_context(self):
        referenced = SimpleNamespace(
            content="original <@123456789012345678>",
            clean_content="original @Ada",
            author=SimpleNamespace(id=301, display_name="Ada", name="ada"),
            attachments=[SimpleNamespace(filename="brief.pdf")],
        )
        message = SimpleNamespace(
            id=500,
            channel=SimpleNamespace(id=10, name="general"),
            guild=SimpleNamespace(id=100, name="Guild"),
            author=SimpleNamespace(id=300, display_name="Human", name="human", bot=False),
            content="this is my reply",
            clean_content="this is my reply",
            attachments=None,
            embeds=None,
            webhook_id=None,
            type=SimpleNamespace(value=19),
            reference=SimpleNamespace(
                message_id=499,
                channel_id=10,
                guild_id=100,
                resolved=referenced,
                cached_message=None,
            ),
        )

        gateway_message = build_gateway_message(message)

        self.assertEqual(
            gateway_message.reply_to,
            {
                "message_id": "499",
                "channel_id": "10",
                "guild_id": "100",
                "author_id": "301",
                "author_name": "Ada",
                "content": "original @Ada",
                "attachment_filenames": ["brief.pdf"],
                "unavailable": False,
            },
        )
        message.type = SimpleNamespace(value=0)
        self.assertIsNone(build_gateway_message(message).reply_to)

    @tag("batch_agent_webhooks")
    def test_gateway_message_builder_preserves_unavailable_reply_id(self):
        message = SimpleNamespace(
            id=500,
            channel=SimpleNamespace(id=10, name="general"),
            guild=SimpleNamespace(id=100, name="Guild"),
            author=SimpleNamespace(id=300, display_name="Human", name="human", bot=False),
            content="reply to a deleted message",
            clean_content="reply to a deleted message",
            attachments=None,
            embeds=None,
            webhook_id=None,
            type=SimpleNamespace(value=19),
            reference=SimpleNamespace(
                message_id=499,
                channel_id=10,
                guild_id=100,
                resolved=None,
                cached_message=None,
            ),
        )

        gateway_message = build_gateway_message(message)

        self.assertEqual(gateway_message.reply_to["message_id"], "499")
        self.assertTrue(gateway_message.reply_to["unavailable"])
        prompt_context = _format_discord_reply_context({"discord_reply_to": gateway_message.reply_to})
        self.assertIn("Discord reply addressee: the referenced message author", prompt_context)
        self.assertIn("clear room-wide invitation in your lane", prompt_context)
        self.assertIn("Once you accept or fetch evidence for it, deliver the result here.", prompt_context)
        self.assertIn("Message ID: 499", prompt_context)
        self.assertIn("referenced message is unavailable or deleted", prompt_context)

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
        self.assertEqual(stored.raw_payload["source_label"], "Human")
        self.assertEqual(stored.raw_payload["discord_content"], "please help @Ada")
        self.assertEqual(stored.raw_payload["discord_raw_content"], "please help <@123456789012345678>")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    @patch("api.services.discord_bot.send_inactive_discord_auto_reply")
    def test_inactive_agent_discord_attempt_is_stored_handled_and_not_dispatched(
        self,
        inactive_reply_mock,
        schedule_mock,
    ):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        self.agent.is_active = False
        self.agent.save(update_fields=["is_active"])
        message = DiscordGatewayMessage(
            message_id="paused-500",
            channel_id="10",
            channel_name="general",
            guild_id="100",
            guild_name="Guild",
            author_id="300",
            author_name="Human",
            content="please help",
            attachments=[],
            embeds=[],
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = ingest_gateway_message(message)

        self.assertEqual(result["processing_blocked_reason"], "agent_inactive")
        stored = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(
            stored.raw_payload["inactive_handling"],
            "agent_inactive_blocked_input",
        )
        schedule_mock.assert_not_called()
        inactive_reply_mock.assert_called_once_with(
            self.agent,
            channel_id="10",
            recipient_key="300",
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.send_channel_message")
    def test_inactive_discord_notice_is_friendly_and_deduplicated(self, send_message_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        self.agent.is_active = False
        self.agent.save(update_fields=["is_active"])

        sent = send_inactive_discord_auto_reply(
            self.agent,
            channel_id="10",
            recipient_key="300",
        )

        self.assertTrue(sent)
        self.assertIn("paused", send_message_mock.call_args.kwargs["body"].lower())
        self.assertIn(
            f"/app/agents/{self.agent.id}",
            send_message_mock.call_args.kwargs["body"],
        )

        notice = PersistentAgentMessage.objects.get(
            owner_agent=self.agent,
            raw_payload__kind="agent_inactive_auto_reply",
        )
        self.assertEqual(notice.latest_status, DeliveryStatus.QUEUED)
        send_message_mock.reset_mock()

        sent_again = send_inactive_discord_auto_reply(
            self.agent,
            channel_id="10",
            recipient_key="300",
        )

        self.assertFalse(sent_again)
        send_message_mock.assert_not_called()

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_inactive_discord_notice_delivers_reserved_message(self, post_mock, get_mock):
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
        self.agent.is_active = False
        self.agent.save(update_fields=["is_active"])

        sent = send_inactive_discord_auto_reply(
            self.agent,
            channel_id="10",
            recipient_key="300",
        )

        self.assertTrue(sent)
        notice = PersistentAgentMessage.objects.get(
            owner_agent=self.agent,
            raw_payload__kind="agent_inactive_auto_reply",
        )
        self.assertEqual(notice.latest_status, DeliveryStatus.SENT)
        self.assertEqual(notice.raw_payload["discord_message_id"], "discord-message-1")

    @tag("batch_agent_webhooks")
    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_reply_context_is_persisted_and_rendered_for_agent(
        self,
        schedule_mock,
        _comms_compacted_mock,
        _steps_compacted_mock,
    ):
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
            content="I agree with that",
            attachments=[],
            embeds=[],
            reply_to={
                "message_id": "499",
                "channel_id": "10",
                "guild_id": "100",
                "author_id": "301",
                "author_name": "Ada",
                "content": "Ship the updated report",
                "attachment_filenames": ["report.pdf"],
                "unavailable": False,
            },
        )

        result = ingest_gateway_message(message)
        stored = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(stored.raw_payload["discord_reply_to"]["message_id"], "499")

        context, _, _ = build_prompt_context(self.agent)
        user_prompt = next(item["content"] for item in context if item["role"] == "user")
        self.assertIn("<discord_message_id>500</discord_message_id>", user_prompt)
        self.assertIn("<discord_channel_id>10</discord_channel_id>", user_prompt)
        self.assertIn("<discord_shared_channel_context>", user_prompt)
        self.assertIn("The message may or may not be for you", user_prompt)
        self.assertIn("<discord_reply_context>", user_prompt)
        self.assertIn("Discord reply addressee: Ada.", user_prompt)
        self.assertIn("instructions and second-person language belong to this addressee", user_prompt)
        self.assertIn("clear room-wide invitation in your lane", user_prompt)
        self.assertIn("contribution only you can provide", user_prompt)
        self.assertIn("Once you accept or fetch evidence for it, deliver the result here.", user_prompt)
        self.assertIn("announcing no action", user_prompt)
        self.assertIn("Message ID: 499", user_prompt)
        self.assertIn("Author: Ada", user_prompt)
        self.assertIn("Ship the updated report", user_prompt)
        self.assertIn("Attachments: report.pdf", user_prompt)

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
        schedule_mock.assert_called_once_with(
            str(self.agent.id),
            inbound_message_id=str(stored.id),
            typing_channel_id="10",
        )

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
    @patch("api.services.discord_bot.schedule_discord_inbound_processing")
    def test_inbound_gateway_message_reuses_existing_discord_delivery_on_retry(self, schedule_mock):
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

        first_result = ingest_gateway_message(message)
        retry_result = ingest_gateway_message(message)

        self.assertFalse(retry_result["ignored"])
        self.assertEqual(retry_result["subscription_count"], 2)
        self.assertEqual(
            PersistentAgentMessage.objects.filter(raw_payload__discord_message_id="500").count(),
            2,
        )
        self.assertCountEqual(
            [delivery["message_id"] for delivery in retry_result["deliveries"]],
            [delivery["message_id"] for delivery in first_result["deliveries"]],
        )
        self.assertCountEqual(
            [delivery["subscription_id"] for delivery in retry_result["deliveries"]],
            [str(first_subscription.id), str(second_subscription.id)],
        )
        self.assertEqual(schedule_mock.call_count, 4)

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
        schedule_mock.assert_called_once_with(
            str(self.agent.id),
            inbound_message_id=result["message_id"],
            typing_channel_id="10",
        )

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
        schedule_mock.assert_called_once_with(
            str(self.agent.id),
            inbound_message_id=str(stored.id),
            typing_channel_id="10",
        )

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
        schedule_mock.assert_called_once_with(
            str(second_agent.id),
            inbound_message_id=result["message_id"],
            typing_channel_id="10",
        )

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
    @patch("api.services.discord_bot.requests.put")
    def test_add_discord_reaction_tool_encodes_unicode_and_custom_emoji(self, put_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        put_mock.return_value = _response(status_code=204)

        unicode_result = execute_add_discord_reaction(
            self.agent,
            {
                "channel_id": "10",
                "message_id": "500",
                "emoji": "👍",
                "will_continue_work": False,
            },
        )
        custom_result = execute_add_discord_reaction(
            self.agent,
            {
                "channel_id": "10",
                "message_id": "501",
                "emoji": "<a:party:123>",
                "will_continue_work": True,
            },
        )

        self.assertEqual(unicode_result["status"], "success")
        self.assertEqual(unicode_result["emoji"], "👍")
        self.assertEqual(unicode_result["discord_message_id"], "500")
        self.assertTrue(unicode_result["auto_sleep_ok"])
        self.assertEqual(custom_result["status"], "success")
        self.assertEqual(custom_result["emoji"], "party:123")
        self.assertNotIn("auto_sleep_ok", custom_result)
        self.assertIn(
            "/channels/10/messages/500/reactions/%F0%9F%91%8D/@me",
            put_mock.call_args_list[0].args[0],
        )
        self.assertIn(
            "/channels/10/messages/501/reactions/party%3A123/@me",
            put_mock.call_args_list[1].args[0],
        )
        self.assertEqual(
            put_mock.call_args_list[0].kwargs["headers"]["Authorization"],
            "Bot discord-bot-token",
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.put")
    def test_add_discord_reaction_requires_active_subscription(self, put_mock):
        result = execute_add_discord_reaction(
            self.agent,
            {
                "channel_id": "10",
                "message_id": "500",
                "emoji": "👍",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("No active native Discord subscription", result["message"])
        put_mock.assert_not_called()

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.put")
    def test_add_discord_reaction_returns_permission_repair_guidance(self, put_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        put_mock.return_value = _response(status_code=403)

        result = execute_add_discord_reaction(
            self.agent,
            {
                "channel_id": "10",
                "message_id": "500",
                "emoji": "👍",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("Add Reactions and Read Message History", result["message"])
        self.assertIn("reconnect Discord", result["message"])

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.put")
    def test_add_discord_reaction_reports_missing_message_and_rejected_emoji(self, put_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )
        put_mock.side_effect = [
            _response(status_code=404),
            _response(status_code=400),
        ]

        missing_result = execute_add_discord_reaction(
            self.agent,
            {
                "channel_id": "10",
                "message_id": "missing",
                "emoji": "👍",
                "will_continue_work": False,
            },
        )
        invalid_result = execute_add_discord_reaction(
            self.agent,
            {
                "channel_id": "10",
                "message_id": "500",
                "emoji": "not-an-emoji",
                "will_continue_work": False,
            },
        )

        self.assertEqual(missing_result["status"], "error")
        self.assertIn("could not find that message", missing_result["message"])
        self.assertEqual(invalid_result["status"], "error")
        self.assertIn("rejected that emoji", invalid_result["message"])

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.put")
    def test_add_discord_reaction_rejects_malformed_custom_emoji(self, put_mock):
        guild = self._guild()
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="general",
        )

        with self.assertRaisesRegex(ValueError, "Custom Discord emoji"):
            add_discord_reaction(
                self.agent,
                channel_id="10",
                message_id="500",
                emoji="<:party:not-an-id>",
            )
        put_mock.assert_not_called()

    @tag("batch_agent_webhooks")
    @patch.dict(os.environ, {"GOBII_ENCRYPTION_KEY": "native-discord-tests"}, clear=False)
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_webhook_outbound_send_uses_agent_identity_and_persists_metadata(self, post_mock, get_mock):
        self.agent.emotion = "🚀"
        self.agent.emotion_expires_at = timezone.now() + timedelta(hours=1)
        self.agent.save(update_fields=["emotion", "emotion_expires_at"])
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
        self.assertEqual(
            marker.signature_hash,
            _webhook_echo_signature(
                webhook_id="wh1",
                channel_id="10",
                username="Discord Agent 🚀",
                body="hello discord",
                attachment_filenames=[],
            ),
        )
        self.assertEqual(message.raw_payload["webhook_echo_marker_id"], str(marker.id))
        send_call = post_mock.call_args_list[1]
        self.assertEqual(send_call.kwargs["json"]["username"], "Discord Agent 🚀")
        self.assertEqual(send_call.kwargs["json"]["content"], "hello discord")
        self.assertEqual(send_call.kwargs["params"], {"wait": "true"})

    @tag("batch_agent_webhooks")
    def test_webhook_username_omits_missing_or_expired_emotion(self):
        self.assertEqual(_agent_webhook_username(self.agent), "Discord Agent")

        self.agent.emotion = "😴"
        self.agent.emotion_expires_at = timezone.now() - timedelta(seconds=1)

        self.assertEqual(_agent_webhook_username(self.agent), "Discord Agent")

    @tag("batch_agent_webhooks")
    def test_webhook_username_truncates_name_without_dropping_emotion(self):
        self.agent.name = "A" * 100
        self.agent.emotion = "👨🏽‍💻"
        self.agent.emotion_expires_at = timezone.now() + timedelta(hours=1)

        username = _agent_webhook_username(self.agent)

        self.assertEqual(len(username), 80)
        self.assertTrue(username.endswith(" 👨🏽‍💻"))

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
    @patch("api.services.discord_bot.requests.get")
    @patch("api.services.discord_bot.requests.post")
    def test_webhook_outbound_send_converts_markdown_table_to_discord_structure(self, post_mock, get_mock):
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
            body=(
                "**Summary**\n\n"
                "| Option | Speed | Risk |\n"
                "| --- | --- | --- |\n"
                "| Alpha | Fastest | High |\n"
                "| Beta | Balanced | Medium |"
            ),
        )

        expected = (
            "**Summary**\n\n"
            "**Alpha**\n"
            "- **Speed:** Fastest\n"
            "- **Risk:** High\n\n"
            "**Beta**\n"
            "- **Speed:** Balanced\n"
            "- **Risk:** Medium"
        )
        self.assertEqual(message.body, expected)
        send_call = post_mock.call_args_list[1]
        self.assertEqual(send_call.kwargs["json"]["content"], expected)

    @tag("batch_agent_webhooks")
    def test_discord_table_normalizer_transposes_matrix_and_preserves_code_fences(self):
        matrix = (
            "| | Alpha | Beta |\n"
            "| --- | --- | --- |\n"
            "| Speed | Fastest | Balanced |\n"
            "| Risk | High | Medium |"
        )
        self.assertEqual(
            normalize_discord_markdown(matrix),
            (
                "**Alpha**\n"
                "- **Speed:** Fastest\n"
                "- **Risk:** High\n\n"
                "**Beta**\n"
                "- **Speed:** Balanced\n"
                "- **Risk:** Medium"
            ),
        )

        fenced = f"```markdown\n{matrix}\n```"
        self.assertEqual(normalize_discord_markdown(fenced), fenced)

    @tag("batch_agent_webhooks")
    def test_discord_table_normalizer_keeps_valid_dense_message_within_content_limit(self):
        headers = ["Company", "Owner", "Stage", "Region", "Status", "Score", "Note"]
        rows = [
            [f"item-{row}", *(f"value-{row}-{column}" for column in range(1, 7))]
            for row in range(15)
        ]
        table = (
            f"| {' | '.join(headers)} |\n"
            f"| {' | '.join(['---'] * len(headers))} |\n"
            + "\n".join(f"| {' | '.join(row)} |" for row in rows)
        )

        self.assertLessEqual(len(table), 2_000)
        normalized = normalize_discord_markdown(table)
        self.assertLessEqual(len(normalized), 2_000)
        self.assertNotIn("| --- |", normalized)

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
        self.agent.emotion = "📎"
        self.agent.emotion_expires_at = timezone.now() + timedelta(hours=1)
        self.agent.save(update_fields=["emotion", "emotion_expires_at"])
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
        self.assertEqual(payload["username"], "Discord Agent 📎")
        self.assertEqual(payload["content"], "")
        self.assertEqual(send_call.kwargs["files"][0][0], "files[0]")
        self.assertEqual(send_call.kwargs["files"][0][1][0], "report.txt")
        self.assertEqual(send_call.kwargs["files"][0][1][2], "text/plain")
        self.assertNotIn("json", send_call.kwargs)
        broadcast_mock.assert_called_once_with(str(message.id))

    @tag("batch_agent_webhooks")
    @patch("api.agent.tools.send_discord_message.resolve_filespace_attachments", return_value=[])
    @patch("api.agent.tools.send_discord_message.send_channel_message")
    def test_send_message_tool_resolves_unique_subscribed_channel_name(self, send_mock, _resolve_mock):
        guild = self._guild(name="Support")
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=guild,
            channel_id="10",
            channel_name="releases",
        )
        send_mock.return_value = SimpleNamespace(
            id="message-1",
            raw_payload={"discord_message_id": "discord-message-1"},
        )

        result = execute_send_discord_message(
            self.agent,
            {
                "channel_name": "#Releases",
                "message": "Shipped.",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["channel_id"], "10")
        self.assertEqual(result["channel_name"], "releases")
        send_mock.assert_called_once_with(
            self.agent,
            channel_id="10",
            body="Shipped.",
            attachments=[],
        )

    @tag("batch_agent_webhooks")
    @patch("api.agent.tools.send_discord_message.resolve_filespace_attachments", return_value=[])
    @patch("api.agent.tools.send_discord_message.send_channel_message")
    def test_send_message_tool_rejects_ambiguous_channel_name(self, send_mock, _resolve_mock):
        first_guild = self._guild(guild_id="100", name="Support")
        second_guild = self._guild(guild_id="200", name="Engineering")
        for guild, channel_id in ((first_guild, "10"), (second_guild, "20")):
            PersistentAgentDiscordChannelSubscription.objects.create(
                agent=self.agent,
                guild=guild,
                channel_id=channel_id,
                channel_name="updates",
            )

        result = execute_send_discord_message(
            self.agent,
            {
                "channel_name": "updates",
                "message": "Shipped.",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("matches more than one subscribed channel", result["message"])
        self.assertIn("guild_id", result["message"])
        send_mock.assert_not_called()

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
        self.assertIn("one Discord server", result["message"])
        self.assertIn("/console/api/discord/oauth/start/", result["connect_url"])
        self.assertNotIn("bot_invite_url", result)
        self.assertTrue(result["auto_sleep_ok"])

    @tag("batch_agent_webhooks")
    def test_subscription_tool_list_guilds_returns_action_required_connect_url_without_claimed_guild(self):
        result = execute_discord_channel_subscriptions(
            self.agent,
            {"action": "list_guilds", "will_continue_work": False},
        )

        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["guilds"], [])
        self.assertIn("one Discord server", result["message"])
        self.assertIn("/console/api/discord/oauth/start/", result["connect_url"])
        self.assertTrue(result["auto_sleep_ok"])

    @tag("batch_agent_webhooks")
    def test_discord_native_system_skill_prefers_native_discord_tools(self):
        skill = get_system_skill_definition("discord_native")

        self.assertIn("discord_channel_subscriptions", skill.tool_names)
        self.assertIn("send_discord_message", skill.tool_names)
        self.assertIn("add_discord_reaction", skill.tool_names)
        self.assertNotIn("pipedream_trigger_subscriptions", skill.tool_names)
        self.assertIn("Use the native Gobii Discord bot tools", skill.prompt_instructions)
        self.assertIn("immediately call `discord_channel_subscriptions`", skill.prompt_instructions)
        self.assertIn("do not ask whether to start setup first", skill.prompt_instructions)
        self.assertIn("Never invent Discord setup links", skill.prompt_instructions)
        self.assertIn("single setup link", skill.prompt_instructions)
        self.assertIn("returned `connect_url` as the repair link", skill.prompt_instructions)
        self.assertIn("To upload files", skill.prompt_instructions)
        self.assertIn("filespace paths or $[/path]", skill.prompt_instructions)
        self.assertIn("Body text never attaches files", skill.prompt_instructions)
        self.assertIn("Use `add_discord_reaction`", skill.prompt_instructions)
        self.assertIn("Only verified Discord senders marked `[can configure]`", skill.prompt_instructions)
        self.assertIn("A direct reply to someone else is not your social moment", skill.prompt_instructions)
        self.assertIn("Discord cannot render tables", skill.prompt_instructions)
        self.assertIn("never send pipe-separated columns with a hyphen-divider row", skill.prompt_instructions)

    @tag("batch_agent_webhooks")
    def test_global_report_contract_allows_a_supported_discord_equivalent(self):
        prompt = _get_system_instruction(self.agent)

        self.assertNotIn("every item/requested field in one channel-appropriate table", prompt)
        self.assertIn(
            "one channel-appropriate structured comparison: a table where supported, headings and bullets where not",
            prompt,
        )

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
        self.assertTrue(payload["oauth_required"])
        self.assertIn("/console/api/discord/oauth/start/", payload["connect_url"])
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key="discord_native",
                is_enabled=True,
            ).exists()
        )

    @tag("batch_agent_webhooks")
    def test_discord_connect_api_reuses_explicit_context_guild(self):
        self._force_login_console_manager()
        self._guild(guild_id="100", name="Support")

        response = self.client.post(reverse("console-agent-discord-connect", args=[self.agent.id]))

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload["oauth_required"])
        self.assertTrue(payload["app"]["connected"])
        self.assertEqual(payload["app"]["guild_count"], 1)

    @tag("batch_agent_webhooks")
    def test_discord_context_summary_includes_explicit_and_configured_legacy_guilds(self):
        self._force_login_console_manager()
        self._guild(guild_id="100", name="Explicit")
        configured_legacy = PersistentAgentDiscordGuild.objects.create(
            guild_id="200",
            name="Configured Legacy",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=configured_legacy,
            channel_id="20",
            status=PersistentAgentDiscordChannelSubscription.Status.ERROR,
        )
        PersistentAgentDiscordGuild.objects.create(
            guild_id="300",
            name="Broad Legacy",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )

        response = self.client.get(reverse("console-discord-context-app"))

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["guild_count"], 2)
        self.assertEqual(
            {guild["guild_id"] for guild in payload["guilds"]},
            {"100", "200"},
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.delete")
    def test_discord_disconnect_api_removes_owner_connections_and_subscriptions(self, delete_mock):
        delete_mock.return_value = _response(status_code=204)
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
    @patch("api.services.discord_bot.requests.delete")
    def test_discord_disconnect_api_reports_partial_external_failure(self, delete_mock):
        self._force_login_console_manager()
        removed_guild = self._guild(guild_id="100", name="A Removed")
        failed_guild = self._guild(guild_id="200", name="B Failed")
        skill_state = PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key="discord_native",
            is_enabled=True,
        )
        success_response = _response(status_code=204)
        failure_response = _response({"message": "Unavailable"}, status_code=503)
        failure_response.text = "Unavailable"
        failure_response.raise_for_status.side_effect = requests.HTTPError("503")
        delete_mock.side_effect = [success_response, failure_response]

        response = self.client.post(reverse("console-discord-disconnect"))

        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertFalse(payload["revoked"])
        self.assertEqual(payload["guilds_disconnected"], 1)
        self.assertEqual(payload["agents_disabled"], 0)
        self.assertEqual(payload["failed_guilds"][0]["guild_id"], "200")
        removed_guild.refresh_from_db()
        failed_guild.refresh_from_db()
        skill_state.refresh_from_db()
        self.assertFalse(removed_guild.is_active)
        self.assertTrue(failed_guild.is_active)
        self.assertTrue(skill_state.is_enabled)

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.delete")
    def test_discord_guild_disconnect_removes_only_requested_server(self, delete_mock):
        delete_mock.return_value = _response(status_code=204)
        self._force_login_console_manager()
        removed_guild = self._guild(guild_id="100", name="Remove")
        kept_guild = self._guild(guild_id="200", name="Keep")
        removed_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=removed_guild,
            channel_id="10",
            channel_name="triage",
        )
        kept_subscription = PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=kept_guild,
            channel_id="20",
            channel_name="general",
        )

        response = self.client.delete(
            reverse("console-discord-guild-disconnect", args=["100"]),
        )

        self.assertEqual(response.status_code, 200, response.content)
        removed_guild.refresh_from_db()
        kept_guild.refresh_from_db()
        removed_subscription.refresh_from_db()
        kept_subscription.refresh_from_db()
        self.assertFalse(removed_guild.is_active)
        self.assertTrue(kept_guild.is_active)
        self.assertEqual(
            removed_subscription.status,
            PersistentAgentDiscordChannelSubscription.Status.DISABLED,
        )
        self.assertEqual(
            kept_subscription.status,
            PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
        )
        self.assertEqual(
            delete_mock.call_args.kwargs["headers"],
            {"Authorization": "Bot discord-bot-token"},
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.delete")
    def test_discord_guild_disconnect_preserves_claim_when_discord_fails(self, delete_mock):
        self._force_login_console_manager()
        guild = self._guild(guild_id="100", name="Support")
        webhook = PersistentAgentDiscordWebhook.objects.create(
            guild=guild,
            channel_id="10",
            webhook_id="webhook-1",
            name="Gobii",
        )
        webhook.webhook_token = "webhook-token"
        webhook.save(update_fields=["webhook_token_encrypted", "updated_at"])
        response = _response({"message": "Unavailable"}, status_code=503)
        response.text = "Unavailable"
        response.raise_for_status.side_effect = requests.HTTPError("503")
        delete_mock.return_value = response

        api_response = self.client.delete(
            reverse("console-discord-guild-disconnect", args=["100"]),
        )

        self.assertEqual(api_response.status_code, 502)
        guild.refresh_from_db()
        self.assertTrue(guild.is_active)
        self.assertTrue(PersistentAgentDiscordWebhook.objects.filter(id=webhook.id).exists())
        self.assertEqual(delete_mock.call_count, 1)
        self.assertIn("/users/@me/guilds/100", delete_mock.call_args.args[0])

    @tag("batch_agent_webhooks")
    def test_cleanup_legacy_discord_guilds_dry_run_reports_without_mutating(self):
        legacy_guild = PersistentAgentDiscordGuild.objects.create(
            guild_id="100",
            name="Legacy",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        configured_legacy = PersistentAgentDiscordGuild.objects.create(
            guild_id="150",
            name="Configured Legacy",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=configured_legacy,
            channel_id="15",
            status=PersistentAgentDiscordChannelSubscription.Status.ERROR,
        )
        self._guild(guild_id="200", name="Explicit")
        stdout = StringIO()

        call_command("cleanup_legacy_discord_guilds", stdout=stdout)

        legacy_guild.refresh_from_db()
        self.assertTrue(legacy_guild.is_active)
        self.assertIn("1 eligible guild", stdout.getvalue())
        self.assertIn("WOULD_REMOVE 100", stdout.getvalue())
        self.assertNotIn("WOULD_REMOVE 150", stdout.getvalue())
        self.assertNotIn("WOULD_REMOVE 200", stdout.getvalue())

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.delete")
    def test_cleanup_legacy_discord_guilds_apply_removes_eligible_claim(self, delete_mock):
        delete_mock.return_value = _response(status_code=404)
        legacy_guild = PersistentAgentDiscordGuild.objects.create(
            guild_id="100",
            name="Legacy",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        stdout = StringIO()

        call_command("cleanup_legacy_discord_guilds", apply=True, stdout=stdout)

        legacy_guild.refresh_from_db()
        self.assertFalse(legacy_guild.is_active)
        self.assertIn("Removed 1 guild", stdout.getvalue())

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
    def test_explicit_guild_migration_retains_selected_and_configured_claims(self):
        migration = importlib.import_module("api.migrations.0442_discord_explicit_guild_authorization")
        selected_guild = PersistentAgentDiscordGuild.objects.create(
            guild_id="100",
            name="Selected",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        configured_guild = PersistentAgentDiscordGuild.objects.create(
            guild_id="200",
            name="Configured",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        broad_guild = PersistentAgentDiscordGuild.objects.create(
            guild_id="300",
            name="Broad",
            owner_user=self.user,
            authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        PersistentAgentDiscordOAuthSession.objects.create(
            state="migration-selected",
            agent=self.agent,
            owner_user=self.user,
            initiated_by=self.user,
            expires_at=timezone.now() + timedelta(minutes=15),
            completed_at=timezone.now(),
            selected_guild_id="100",
        )
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=self.agent,
            guild=configured_guild,
            channel_id="20",
            status=PersistentAgentDiscordChannelSubscription.Status.ERROR,
        )

        class Apps:
            models = {
                "PersistentAgentDiscordGuild": PersistentAgentDiscordGuild,
                "PersistentAgentDiscordOAuthSession": PersistentAgentDiscordOAuthSession,
                "PersistentAgentDiscordChannelSubscription": PersistentAgentDiscordChannelSubscription,
            }

            @classmethod
            def get_model(cls, app_label, model_name):
                self.assertEqual(app_label, "api")
                return cls.models[model_name]

        migration.classify_existing_discord_guild_claims(Apps(), None)

        selected_guild.refresh_from_db()
        configured_guild.refresh_from_db()
        broad_guild.refresh_from_db()
        self.assertEqual(
            selected_guild.authorization_source,
            PersistentAgentDiscordGuild.AuthorizationSource.EXPLICIT_OAUTH,
        )
        self.assertEqual(
            configured_guild.authorization_source,
            PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )
        self.assertEqual(
            broad_guild.authorization_source,
            PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
        )

    @tag("batch_agent_webhooks")
    def test_discord_reaction_migration_backfills_enabled_skill_agents(self):
        migration = importlib.import_module("api.migrations.0431_enable_discord_reaction_tool")
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key="discord_native",
            is_enabled=True,
        )

        class Apps:
            @staticmethod
            def get_model(app_label, model_name):
                self.assertEqual(app_label, "api")
                models = {
                    "PersistentAgentEnabledTool": PersistentAgentEnabledTool,
                    "PersistentAgentSystemSkillState": PersistentAgentSystemSkillState,
                }
                return models[model_name]

        migration.enable_discord_reaction_tool(Apps(), None)
        migration.enable_discord_reaction_tool(Apps(), None)

        enabled = PersistentAgentEnabledTool.objects.get(
            agent=self.agent,
            tool_full_name="add_discord_reaction",
        )
        self.assertEqual(enabled.tool_server, "builtin")
        self.assertEqual(enabled.tool_name, "add_discord_reaction")

    @tag("batch_agent_webhooks")
    @patch("api.services.discord_bot.requests.get")
    def test_discover_channels_returns_scoped_reconnect_url_when_bot_cannot_list_channels(self, get_mock):
        self._guild()
        response = _response({"message": "Missing Access"}, status_code=403)
        response.raise_for_status.side_effect = requests.HTTPError("403")
        get_mock.return_value = response

        result = discover_channels(self.agent)

        self.assertEqual(result["status"], "action_required")
        self.assertIn("cannot list channels", result["message"])
        self.assertIn("/console/api/discord/oauth/start/", result["connect_url"])
        self.assertIn("guild_id=100", result["connect_url"])
