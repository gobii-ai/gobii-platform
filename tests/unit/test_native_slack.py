import hashlib
import hmac
import json
import os
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from waffle.models import Flag

from api.agent.system_skills.defaults import SLACK_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.registry import get_system_skill_definition
from api.agent.tools.send_slack_message import execute_send_slack_message
from api.agent.tools.slack_channel_subscriptions import execute_slack_channel_subscriptions
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    GlobalSecret,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentMessage,
    PersistentAgentSlackChannelSubscription,
    PersistentAgentSlackEventReceipt,
    PersistentAgentSlackWorkspace,
    PersistentAgentSystemSkillState,
    PipedreamAppSelection,
)
from api.services.native_integrations import (
    NATIVE_INTEGRATION_PIPEDREAM_APP_SLUGS,
    SLACK_PROVIDER,
    get_native_integration_provider,
)
from api.services.pipedream_apps import PIPEDREAM_RUNTIME_NAME
from api.services.slack_bot import (
    SlackEventMessage,
    discover_channels,
    ensure_subscription,
    ingest_event_message,
    send_channel_message,
    slack_event_message_from_payload,
)
from api.services.slack_messages import (
    create_slack_outbound_message,
    slack_channel_address,
    slack_conversation_address,
)
from console.agent_chat.timeline import serialize_message_event


User = get_user_model()


def _ensure_encryption_key():
    if not os.environ.get("GOBII_ENCRYPTION_KEY"):
        os.environ["GOBII_ENCRYPTION_KEY"] = "test-key-for-native-slack"


def _response(payload=None, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    response.raise_for_status.return_value = None
    return response


def _httpx_token_response(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


def _slack_signature(body: bytes, secret: str, timestamp: str | None = None):
    request_timestamp = timestamp or str(int(time.time()))
    base = b"v0:" + request_timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return request_timestamp, f"v0={digest}"


@tag("batch_native_slack")
@override_settings(
    SLACK_CLIENT_ID="slack-client",
    SLACK_CLIENT_SECRET="slack-secret",
    SLACK_SIGNING_SECRET="slack-signing-secret",
    SLACK_OAUTH_SCOPES=(
        "chat:write",
        "chat:write.customize",
        "channels:read",
        "channels:history",
        "groups:read",
        "groups:history",
        "users:read",
    ),
    SLACK_INBOUND_DEBOUNCE_SECONDS=15,
    PUBLIC_SITE_URL="https://app.example.test",
    CELERY_TASK_ALWAYS_EAGER=True,
    GOBII_PROPRIETARY_MODE=False,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class NativeSlackTests(TestCase):
    def setUp(self):
        _ensure_encryption_key()
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})
        self.user = User.objects.create_user(
            username="slack-owner",
            email="slack-owner@example.test",
            password="password123",
        )
        self.org = Organization.objects.create(
            name="Slack Org",
            slug="slack-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Slack Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Ada Slack",
            charter="Handle Slack messages.",
            browser_use_agent=self.browser_agent,
        )
        self.client.force_login(self.user)

    def _credentials(self, *, access_token="xoxb-token", scope=None, expires_at=None):
        return {
            "provider_key": SLACK_PROVIDER.key,
            "auth_type": "oauth2",
            "access_token": access_token,
            "refresh_token": "",
            "token_type": "Bearer",
            "scope": scope if scope is not None else SLACK_PROVIDER.scope_string,
            "expires_at": expires_at or (timezone.now() + timedelta(hours=1)).isoformat(),
        }

    def _create_secret(self, *, owner_user=None, owner_org=None, credentials=None):
        secret = GlobalSecret(
            user=owner_user,
            organization=owner_org,
            name=SLACK_PROVIDER.display_name,
            description=SLACK_PROVIDER.description,
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=SLACK_PROVIDER.secret_key,
        )
        secret.set_value(json.dumps(credentials or self._credentials()))
        secret.save()
        return secret

    def _workspace(self, *, team_id="T123", team_name="Acme Slack"):
        return PersistentAgentSlackWorkspace.objects.create(
            team_id=team_id,
            team_name=team_name,
            owner_user=self.user,
            claimed_by=self.user,
        )

    def _active_subscription(self, *, channel_id="C123", channel_name="general"):
        workspace = self._workspace()
        return PersistentAgentSlackChannelSubscription.objects.create(
            agent=self.agent,
            workspace=workspace,
            channel_id=channel_id,
            channel_name=channel_name,
            channel_type="public_channel",
        )

    def _start_oauth(self):
        response = self.client.post(reverse("console-native-integration-connect", args=["slack"]))
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def _post_oauth_callback(self, state):
        return self.client.post(
            reverse("console-native-integration-callback", args=["slack"]),
            data=json.dumps({"authorization_code": "auth-code", "state": state}),
            content_type="application/json",
        )

    def test_provider_registry_scopes_and_pipedream_overlap(self):
        provider = get_native_integration_provider("slack")

        self.assertEqual(provider.display_name, "Slack")
        self.assertIn("chat:write.customize", provider.scopes)
        self.assertIn("channels:history", provider.scopes)
        self.assertIn("users:read", provider.scopes)
        self.assertEqual(provider.api_url_prefixes, ("https://slack.com/api/",))
        self.assertEqual(NATIVE_INTEGRATION_PIPEDREAM_APP_SLUGS["slack"], ("slack",))

        skill = get_system_skill_definition(SLACK_NATIVE_SYSTEM_SKILL_KEY)
        self.assertEqual(skill.tool_names, ("slack_channel_subscriptions", "send_slack_message"))
        self.assertIn("display-only", skill.prompt_instructions)
        self.assertIn("discover_channels", skill.prompt_instructions)
        self.assertIn("Do not use Pipedream Slack tools", skill.prompt_instructions)

    @patch("api.services.native_integrations.httpx.post")
    def test_oauth_callback_stores_credentials_claims_workspace_and_disables_pipedream(self, post_mock):
        PipedreamAppSelection.objects.create(user=self.user, selected_app_slugs=["slack", "github"])
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="slack-post-message",
            tool_name="slack-post-message",
            tool_server=PIPEDREAM_RUNTIME_NAME,
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="github-create-issue",
            tool_name="github-create-issue",
            tool_server=PIPEDREAM_RUNTIME_NAME,
        )
        start = self._start_oauth()
        self.assertIn("scope=chat%3Awrite%2Cchat%3Awrite.customize", start["authorization_url"])
        post_mock.return_value = _httpx_token_response(
            {
                "ok": True,
                "access_token": "xoxb-new",
                "token_type": "bot",
                "scope": SLACK_PROVIDER.scope_string,
                "team": {"id": "T123", "name": "Acme Slack"},
                "enterprise": {"id": "E123", "name": "Enterprise"},
                "app_id": "A123",
                "bot_user_id": "B123",
            }
        )

        response = self._post_oauth_callback(start["state"])

        self.assertEqual(response.status_code, 200, response.content)
        workspace = PersistentAgentSlackWorkspace.objects.get(team_id="T123")
        self.assertEqual(workspace.team_name, "Acme Slack")
        self.assertEqual(workspace.bot_user_id, "B123")
        secret = GlobalSecret.objects.get(key=SLACK_PROVIDER.secret_key, user=self.user)
        stored = json.loads(secret.get_value())
        self.assertEqual(stored["access_token"], "xoxb-new")
        enabled_tools = set(PersistentAgentEnabledTool.objects.values_list("tool_full_name", flat=True))
        self.assertEqual(enabled_tools, {"github-create-issue"})
        selected_slugs = set(PipedreamAppSelection.objects.get(user=self.user).selected_app_slugs)
        self.assertEqual(selected_slugs, {"github"})

    @patch("api.services.slack_bot.requests.get")
    def test_channel_discovery_paginates_filters_supported_channels_and_disconnected_guidance(self, get_mock):
        disconnected = discover_channels(self.agent)
        self.assertEqual(disconnected["status"], "action_required")
        self.assertEqual(disconnected["channels"], [])

        self._create_secret(owner_user=self.user)
        self._workspace()
        get_mock.side_effect = [
            _response(
                {
                    "ok": True,
                    "channels": [
                        {"id": "C1", "name": "general", "is_private": False},
                        {"id": "G1", "name": "private-room", "is_private": True},
                    ],
                    "response_metadata": {"next_cursor": "next"},
                }
            ),
            _response(
                {
                    "ok": True,
                    "channels": [
                        {"id": "D1", "name": "dm", "is_im": True},
                        {"id": "C2", "name": "random", "is_private": False},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            ),
        ]

        result = discover_channels(self.agent, query="general", limit=200)

        self.assertEqual(result["status"], "success")
        self.assertEqual([channel["channel_id"] for channel in result["channels"]], ["C1"])
        self.assertEqual(result["channels"][0]["channel_type"], "public_channel")
        self.assertEqual(get_mock.call_count, 2)

    def test_subscription_tool_ensure_list_disable_and_uniqueness(self):
        workspace = self._workspace()

        ensure_result = execute_slack_channel_subscriptions(
            self.agent,
            {
                "action": "ensure",
                "workspace_id": str(workspace.id),
                "channel_id": "C123",
                "channel_name": "general",
                "channel_type": "public_channel",
                "will_continue_work": True,
            },
        )
        self.assertEqual(ensure_result["status"], "success")
        self.assertTrue(ensure_result["created"])

        reused_result = ensure_subscription(
            self.agent,
            workspace_id=str(workspace.id),
            channel_id="C123",
            channel_name="general",
            channel_type="public_channel",
        )
        self.assertTrue(reused_result["reused"])

        list_result = execute_slack_channel_subscriptions(
            self.agent,
            {"action": "list", "will_continue_work": True},
        )
        self.assertEqual(len(list_result["subscriptions"]), 1)

        disable_result = execute_slack_channel_subscriptions(
            self.agent,
            {
                "action": "disable",
                "subscription_id": list_result["subscriptions"][0]["id"],
                "will_continue_work": False,
            },
        )
        self.assertEqual(disable_result["subscription"]["status"], "disabled")
        self.assertTrue(disable_result["auto_sleep_ok"])

    @patch("api.services.slack_bot.requests.post")
    def test_send_message_posts_customized_name_and_records_outbound(self, post_mock):
        self._create_secret(owner_user=self.user)
        self._active_subscription(channel_id="C123", channel_name="general")
        post_mock.return_value = _response({"ok": True, "ts": "171000.123"})

        message = send_channel_message(self.agent, channel_id="C123", body="hello Slack")

        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["channel"], "C123")
        self.assertEqual(payload["text"], "hello Slack")
        self.assertEqual(payload["username"], "Ada Slack")
        self.assertNotIn("icon_url", payload)
        self.assertEqual(message.body, "hello Slack")
        self.assertTrue(message.is_outbound)
        self.assertEqual(message.raw_payload["slack_response"]["ts"], "171000.123")

    @patch("api.services.slack_bot.requests.post")
    def test_send_tool_returns_actionable_slack_error(self, post_mock):
        self._create_secret(owner_user=self.user)
        self._active_subscription(channel_id="C123", channel_name="general")
        post_mock.return_value = _response({"ok": False, "error": "missing_scope", "needed": "chat:write.customize"})

        result = execute_send_slack_message(
            self.agent,
            {"channel_id": "C123", "message": "hello", "will_continue_work": True},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("requires additional scopes", result["message"])
        self.assertIn("chat:write.customize", result["message"])
        self.assertIn("Reconnect Slack", result["message"])

    @patch("api.services.slack_bot.schedule_slack_inbound_processing")
    def test_events_endpoint_verifies_signature_ingests_dedupes_and_ignores_subtypes(self, schedule_mock):
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        self._active_subscription(channel_id="C123", channel_name="general")
        payload = {
            "type": "event_callback",
            "event_id": "Ev123",
            "team_id": "T123",
            "event": {
                "type": "message",
                "channel": "C123",
                "channel_type": "channel",
                "channel_name": "general",
                "user": "U123",
                "user_profile": {"display_name": "Mira Example", "real_name": "Mira Real"},
                "text": "hello from human",
                "ts": "171000.123",
            },
        }
        body = json.dumps(payload).encode("utf-8")
        timestamp, signature = _slack_signature(body, "slack-signing-secret")

        response = self.client.post(
            reverse("slack-events"),
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["ignored"])
        stored = PersistentAgentMessage.objects.get(body="hello from human")
        self.assertEqual(stored.conversation.channel, CommsChannel.SLACK)
        self.assertEqual(stored.raw_payload["slack_event_id"], "Ev123")
        self.assertEqual(stored.raw_payload["slack_author_name"], "Mira Example")
        message_payload = serialize_message_event(stored)["message"]
        self.assertEqual(message_payload["senderName"], "Mira Example")
        self.assertEqual(message_payload["sourceLabel"], "#general")
        schedule_mock.assert_called_once_with(str(self.agent.id))

        duplicate = self.client.post(
            reverse("slack-events"),
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()["ignored"])
        self.assertEqual(PersistentAgentSlackEventReceipt.objects.count(), 1)

        subtype_payload = {
            "type": "event_callback",
            "event_id": "Ev124",
            "team_id": "T123",
            "event": {
                "type": "message",
                "subtype": "message_changed",
                "channel": "C123",
                "channel_type": "channel",
                "user": "U123",
                "text": "edited",
            },
        }
        self.assertIsNone(slack_event_message_from_payload(subtype_payload))

        bot_payload = {
            "type": "event_callback",
            "event_id": "Ev125",
            "team_id": "T123",
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel": "C123",
                "channel_type": "channel",
                "bot_id": "B123",
                "app_id": "A123",
                "username": "Ada Slack",
                "text": "hello from an agent",
                "ts": "171000.456",
            },
        }
        bot_message = slack_event_message_from_payload(bot_payload)
        self.assertIsNotNone(bot_message)
        self.assertEqual(bot_message.user_id, "B123")

    @patch("api.services.slack_bot.schedule_slack_inbound_processing")
    @patch("api.services.slack_bot.requests.get")
    def test_event_ingestion_fetches_slack_user_profile_when_event_lacks_name(self, get_mock, schedule_mock):
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        self._create_secret(owner_user=self.user)
        self._active_subscription(channel_id="C123", channel_name="general")
        get_mock.return_value = _response(
            {
                "ok": True,
                "user": {
                    "id": "U123",
                    "name": "mira",
                    "profile": {
                        "display_name": "Mira Profile",
                        "real_name": "Mira Real",
                    },
                },
            }
        )
        message = SlackEventMessage(
            event_id="EvProfile",
            team_id="T123",
            channel_id="C123",
            channel_name="general",
            channel_type="channel",
            user_id="U123",
            text="profile lookup",
            ts="171000.456",
            thread_ts="",
            raw_event={
                "type": "message",
                "channel": "C123",
                "channel_type": "channel",
                "user": "U123",
                "text": "profile lookup",
                "ts": "171000.456",
            },
        )

        ingest_event_message(message)

        stored = PersistentAgentMessage.objects.get(body="profile lookup")
        self.assertEqual(stored.raw_payload["sender_name"], "Mira Profile")
        self.assertEqual(serialize_message_event(stored)["message"]["senderName"], "Mira Profile")
        self.assertEqual(get_mock.call_args.args[0], "https://slack.com/api/users.info")
        self.assertEqual(get_mock.call_args.kwargs["params"], {"user": "U123"})

    def test_events_endpoint_handles_url_verification_and_rejects_bad_or_stale_signatures(self):
        body = json.dumps({"type": "url_verification", "challenge": "challenge-token"}).encode("utf-8")
        timestamp, signature = _slack_signature(body, "slack-signing-secret")

        response = self.client.post(
            reverse("slack-events"),
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["challenge"], "challenge-token")

        bad = self.client.post(
            reverse("slack-events"),
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE="v0=bad",
        )
        self.assertEqual(bad.status_code, 403)

        stale_timestamp = str(int(time.time()) - 600)
        stale_timestamp, stale_signature = _slack_signature(body, "slack-signing-secret", stale_timestamp)
        stale = self.client.post(
            reverse("slack-events"),
            data=body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=stale_timestamp,
            HTTP_X_SLACK_SIGNATURE=stale_signature,
        )
        self.assertEqual(stale.status_code, 403)

    @patch("api.services.slack_bot.schedule_slack_inbound_processing")
    def test_ingest_event_fans_out_to_all_subscribed_agents(self, schedule_mock):
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        workspace = self._workspace()
        second_browser = BrowserUseAgent.objects.create(user=self.user, name="Second Browser")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Second Slack",
            charter="Also handle Slack.",
            browser_use_agent=second_browser,
        )
        for agent in (self.agent, second_agent):
            PersistentAgentSlackChannelSubscription.objects.create(
                agent=agent,
                workspace=workspace,
                channel_id="C123",
                channel_name="general",
                channel_type="public_channel",
            )
        message = SlackEventMessage(
            event_id="EvFanout",
            team_id="T123",
            channel_id="C123",
            channel_name="general",
            channel_type="channel",
            user_id="U123",
            text="fan out",
            ts="171000.123",
            thread_ts="",
            raw_event={"type": "message"},
        )

        result = ingest_event_message(message)

        self.assertFalse(result["ignored"])
        self.assertEqual(result["subscription_count"], 2)
        self.assertEqual(
            set(PersistentAgentMessage.objects.filter(body="fan out").values_list("owner_agent_id", flat=True)),
            {self.agent.id, second_agent.id},
        )

    @patch("api.services.slack_bot.schedule_slack_inbound_processing")
    def test_agent_slack_bot_message_fans_out_to_other_subscribed_agents_only(self, schedule_mock):
        schedule_mock.return_value = {"debounced": True, "debounce_seconds": 15}
        workspace = self._workspace()
        workspace.app_id = "A123"
        workspace.bot_user_id = "Ubot"
        workspace.save(update_fields=["app_id", "bot_user_id", "updated_at"])
        second_browser = BrowserUseAgent.objects.create(user=self.user, name="Second Browser")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Second Slack",
            charter="Also handle Slack.",
            browser_use_agent=second_browser,
        )
        for agent in (self.agent, second_agent):
            PersistentAgentSlackChannelSubscription.objects.create(
                agent=agent,
                workspace=workspace,
                channel_id="C123",
                channel_name="general",
                channel_type="public_channel",
            )
        create_slack_outbound_message(
            self.agent,
            channel_id="C123",
            body="hello from sender",
            conversation_address=slack_conversation_address(self.agent.id, "T123", "C123"),
            platform_channel_address=slack_channel_address("T123", "C123"),
            channel_name="general",
            raw_payload={
                "source": "slack_bot_api",
                "slack_message_ts": "171000.999",
                "slack_team_id": "T123",
                "slack_channel_id": "C123",
            },
        )
        payload = {
            "type": "event_callback",
            "event_id": "EvAgentBot",
            "team_id": "T123",
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel": "C123",
                "channel_type": "channel",
                "channel_name": "general",
                "bot_id": "B123",
                "app_id": "A123",
                "username": "Ada Slack",
                "text": "hello from sender",
                "ts": "171000.999",
            },
        }

        message = slack_event_message_from_payload(payload)
        self.assertIsNotNone(message)
        result = ingest_event_message(message)

        self.assertFalse(result["ignored"])
        self.assertEqual(result["subscription_count"], 1)
        self.assertEqual(len(result["deliveries"]), 1)
        self.assertEqual(result["deliveries"][0]["agent_id"], str(second_agent.id))
        inbound = PersistentAgentMessage.objects.get(owner_agent=second_agent, body="hello from sender")
        self.assertFalse(inbound.is_outbound)
        self.assertEqual(inbound.raw_payload["slack_author_name"], "Ada Slack")
        self.assertFalse(
            PersistentAgentMessage.objects.filter(
                owner_agent=self.agent,
                body="hello from sender",
                is_outbound=False,
            ).exists()
        )

    def test_slack_app_endpoints_enable_skill_and_replace_subscriptions(self):
        workspace = self._workspace()

        connect_response = self.client.post(reverse("console-agent-slack-connect", args=[self.agent.id]))
        self.assertEqual(connect_response.status_code, 200, connect_response.content)
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=SLACK_NATIVE_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )

        response = self.client.post(
            reverse("console-agent-slack-subscriptions", args=[self.agent.id]),
            data=json.dumps(
                {
                    "subscriptions": [
                        {
                            "workspace_id": str(workspace.id),
                            "channel_id": "C123",
                            "channel_name": "general",
                            "channel_type": "public_channel",
                        }
                    ]
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["active_subscription_count"], 1)

        empty_response = self.client.post(
            reverse("console-agent-slack-subscriptions", args=[self.agent.id]),
            data=json.dumps({"subscriptions": []}),
            content_type="application/json",
        )
        self.assertEqual(empty_response.status_code, 200, empty_response.content)
        self.assertEqual(empty_response.json()["active_subscription_count"], 0)
