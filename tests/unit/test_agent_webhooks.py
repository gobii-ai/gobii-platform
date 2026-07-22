from unittest.mock import MagicMock, patch
import json

from allauth.account.models import EmailAddress
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from requests import RequestException

from api.agent.tools.webhook_sender import execute_send_webhook_event
from api.agent.tools.webhook_management import (
    execute_manage_inbound_webhooks,
    execute_manage_outbound_webhooks,
)
from api.agent.tools.sqlite_skills import format_recent_skills_for_prompt
from api.agent.tools.static_tools import get_static_tool_names, planning_mode_disallows_tool
from api.agent.tools.tool_manager import (
    execute_enabled_tool,
    get_available_builtin_tool_entries,
    get_enabled_tool_definitions,
)
from api.agent.system_skills.defaults import WEBHOOKS_SYSTEM_SKILL_KEY
from api.agent.system_skills.registry import shortlist_system_skills
from api.agent.system_skills.service import enable_system_skills, get_available_system_skill_tool_names
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentInboundWebhook,
    PersistentAgentMessage,
    PersistentAgentEnabledTool,
    PersistentAgentSystemSkillState,
    PersistentAgentWebhook,
    ProxyServer,
)
from api.webhooks import _parse_inbound_agent_webhook_request
from util.analytics import AnalyticsEvent, AnalyticsSource


class AgentWebhookToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="webhook-owner",
            email="owner@example.com",
            password="password123",
        )
        # Email verification is required for webhook sending
        EmailAddress.objects.create(
            user=cls.user,
            email=cls.user.email,
            verified=True,
            primary=True,
        )
        cls.proxy = ProxyServer.objects.create(
            name="Webhook Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="proxy.example.com",
            port=8080,
        )
        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Browser Agent",
            preferred_proxy=cls.proxy,
        )
        agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Webhook Tester",
            charter="Test webhook delivery",
            browser_use_agent=cls.browser_agent,
        )
        webhook = PersistentAgentWebhook.objects.create(
            agent=agent,
            name="Status Hook",
            url="https://example.com/hook",
        )
        cls.agent_id = agent.id
        cls.webhook_id = webhook.id

    def setUp(self):
        self.agent = PersistentAgent.objects.get(pk=self.agent_id)
        self.webhook = PersistentAgentWebhook.objects.get(pk=self.webhook_id)
        self.proxy = type(self).proxy

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_success(self):
        with patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            mock_response = MagicMock(status_code=204, text="")
            mock_post.return_value = mock_response

            payload = {"status": "ok"}
            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": payload},
            )

            self.assertEqual(result.get("status"), "success")
            self.assertEqual(result.get("webhook_id"), str(self.webhook.id))
            self.assertEqual(result.get("response_status"), 204)

            self.webhook.refresh_from_db()
            self.assertIsNotNone(self.webhook.last_triggered_at)
            self.assertEqual(self.webhook.last_response_status, 204)
            self.assertEqual(self.webhook.last_error_message, "")

            called_kwargs = mock_post.call_args.kwargs
            self.assertEqual(called_kwargs["json"], payload)
            self.assertEqual(called_kwargs["headers"]["User-Agent"], "Gobii-AgentWebhook/1.0")
            self.assertEqual(
                called_kwargs["proxies"],
                {"http": self.proxy.proxy_url, "https": self.proxy.proxy_url},
            )

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_http_error(self):
        with patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            mock_response = MagicMock(status_code=500, text="boom")
            mock_post.return_value = mock_response

            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"value": 1}},
            )

            self.assertEqual(result.get("status"), "error")
            self.assertEqual(result.get("response_status"), 500)

            self.webhook.refresh_from_db()
            self.assertEqual(self.webhook.last_response_status, 500)
            self.assertIn("boom", self.webhook.last_error_message)

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_request_exception(self):
        self.webhook.url = "https://example.com/hook?token=private-token"
        self.webhook.save(update_fields=["url", "updated_at"])
        with patch("api.agent.tools.webhook_sender.requests.post") as mock_post, patch(
            "api.agent.tools.webhook_sender.Analytics.track_event"
        ) as mock_track_event:
            mock_post.side_effect = RequestException("timeout with url: /hook?token=private-token")

            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"value": 1}},
            )

            self.assertEqual(result.get("status"), "error")
            self.assertIn("timeout", result.get("message", ""))
            self.assertNotIn(self.webhook.url, result.get("message", ""))
            self.assertNotIn("private-token", result.get("message", ""))

            self.webhook.refresh_from_db()
            self.assertIsNone(self.webhook.last_response_status)
            self.assertIn("timeout", self.webhook.last_error_message)
            self.assertNotIn(self.webhook.url, self.webhook.last_error_message)
            self.assertNotIn("private-token", self.webhook.last_error_message)
            self.assertNotIn(
                self.webhook.url,
                json.dumps(mock_track_event.call_args.kwargs["properties"]),
            )

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_redacts_hostname_from_connection_error(self):
        self.webhook.url = "https://private-hooks.example.com/events"
        self.webhook.save(update_fields=["url", "updated_at"])
        error = "Failed to resolve 'private-hooks.example.com'"
        with patch("api.agent.tools.webhook_sender.requests.post", side_effect=RequestException(error)):
            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"value": 1}},
            )

        self.webhook.refresh_from_db()
        self.assertNotIn("private-hooks.example.com", result["message"])
        self.assertNotIn("private-hooks.example.com", self.webhook.last_error_message)

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_requires_proxy(self):
        with patch(
            "api.agent.tools.webhook_sender.select_proxy_for_persistent_agent",
            return_value=None,
        ) as mock_select, patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"value": 1}},
            )

        mock_select.assert_called_once_with(self.agent, allow_no_proxy_in_debug=False)
        mock_post.assert_not_called()
        self.assertEqual(result.get("status"), "error")
        self.assertIn("proxy", result.get("message", ""))

        self.webhook.refresh_from_db()
        self.assertIsNone(self.webhook.last_response_status)
        self.assertIn("proxy", self.webhook.last_error_message)

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_requires_json_object(self):
        result = execute_send_webhook_event(
            self.agent,
            {"webhook_id": str(self.webhook.id), "payload": "not-a-dict"},
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Payload must be a JSON object", result.get("message", ""))

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_supports_socks5_proxy(self):
        socks_proxy = ProxyServer.objects.create(
            name="Webhook SOCKS Proxy",
            proxy_type=ProxyServer.ProxyType.SOCKS5,
            host="proxy.example.com",
            port=1080,
        )
        with patch(
            "api.agent.tools.webhook_sender.select_proxy_for_persistent_agent",
            return_value=socks_proxy,
        ), patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204, text="")

            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"status": "ok"}},
            )

        self.assertEqual(result.get("status"), "success")
        self.assertEqual(
            mock_post.call_args.kwargs["proxies"],
            {"http": socks_proxy.proxy_url, "https": socks_proxy.proxy_url},
        )

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_unknown_webhook(self):
        result = execute_send_webhook_event(
            self.agent,
            {"webhook_id": "00000000-0000-0000-0000-000000000000", "payload": {}},
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Webhook not found", result.get("message", ""))


@tag("batch_agent_webhooks")
@override_settings(PUBLIC_SITE_URL="https://gobii.test")
class AgentWebhookManagementToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="webhook-manager",
            email="manager@example.com",
            password="password123",
        )
        cls.other_user = user_model.objects.create_user(
            username="other-webhook-manager",
            email="other-manager@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Webhook Manager Browser")
        cls.other_browser_agent = BrowserUseAgent.objects.create(
            user=cls.other_user,
            name="Other Webhook Manager Browser",
        )
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Webhook Manager",
            charter="Manage native webhooks",
            browser_use_agent=cls.browser_agent,
        )
        cls.other_agent = PersistentAgent.objects.create(
            user=cls.other_user,
            name="Other Webhook Manager",
            charter="Own separate webhooks",
            browser_use_agent=cls.other_browser_agent,
        )

    def test_inbound_management_lifecycle_limits_secret_url_exposure(self):
        created = execute_manage_inbound_webhooks(
            self.agent,
            {
                "action": "create",
                "name": "Aimfox",
                "is_active": True,
                "will_continue_work": True,
            },
        )
        self.assertEqual(created["status"], "success")
        created_webhook = created["webhook"]
        self.assertEqual(created_webhook["name"], "Aimfox")
        self.assertIn("https://gobii.test/api/v1/webhooks/inbound/agents/", created_webhook["url"])
        self.assertIn("?t=", created_webhook["url"])

        listed = execute_manage_inbound_webhooks(
            self.agent,
            {"action": "list", "will_continue_work": True},
        )
        self.assertEqual(len(listed["webhooks"]), 1)
        self.assertNotIn("url", listed["webhooks"][0])

        webhook_id = created_webhook["id"]
        inspected = execute_manage_inbound_webhooks(
            self.agent,
            {"action": "get", "webhook_id": webhook_id, "will_continue_work": True},
        )
        original_url = inspected["webhook"]["url"]
        updated = execute_manage_inbound_webhooks(
            self.agent,
            {
                "action": "update",
                "webhook_id": webhook_id,
                "is_active": False,
                "will_continue_work": True,
            },
        )
        self.assertFalse(updated["webhook"]["is_active"])
        self.assertNotIn("url", updated["webhook"])

        rotated = execute_manage_inbound_webhooks(
            self.agent,
            {"action": "rotate_secret", "webhook_id": webhook_id, "will_continue_work": True},
        )
        self.assertNotEqual(rotated["webhook"]["url"], original_url)

        deleted = execute_manage_inbound_webhooks(
            self.agent,
            {"action": "delete", "webhook_id": webhook_id, "will_continue_work": False},
        )
        self.assertEqual(deleted["status"], "success")
        self.assertTrue(deleted["auto_sleep_ok"])
        self.assertNotIn("url", deleted["deleted_webhook"])
        self.assertFalse(PersistentAgentInboundWebhook.objects.filter(id=webhook_id).exists())

    def test_outbound_management_lifecycle_omits_url_from_list(self):
        created = execute_manage_outbound_webhooks(
            self.agent,
            {
                "action": "create",
                "name": "Operations",
                "url": "https://hooks.example.com/operations?token=secret",
                "will_continue_work": True,
            },
        )
        self.assertEqual(created["status"], "success")
        webhook_id = created["webhook"]["id"]
        self.assertEqual(created["webhook"]["url"], "https://hooks.example.com/operations?token=secret")
        PersistentAgentWebhook.objects.filter(id=webhook_id).update(
            last_error_message="Failed to reach https://hooks.example.com/operations?token=secret",
        )

        listed = execute_manage_outbound_webhooks(
            self.agent,
            {"action": "list", "will_continue_work": True},
        )
        self.assertNotIn("url", listed["webhooks"][0])
        self.assertNotIn("last_error_message", listed["webhooks"][0])
        self.assertNotIn("token=secret", json.dumps(listed))

        updated = execute_manage_outbound_webhooks(
            self.agent,
            {
                "action": "update",
                "webhook_id": webhook_id,
                "url": "https://hooks.example.com/new",
                "will_continue_work": True,
            },
        )
        self.assertEqual(updated["webhook"]["url"], "https://hooks.example.com/new")
        self.assertEqual(
            execute_manage_outbound_webhooks(
                self.agent,
                {"action": "get", "webhook_id": webhook_id, "will_continue_work": True},
            )["webhook"]["url"],
            "https://hooks.example.com/new",
        )

        deleted = execute_manage_outbound_webhooks(
            self.agent,
            {"action": "delete", "webhook_id": webhook_id, "will_continue_work": False},
        )
        self.assertEqual(deleted["status"], "success")
        self.assertNotIn("url", deleted["deleted_webhook"])
        self.assertFalse(PersistentAgentWebhook.objects.filter(id=webhook_id).exists())

    def test_management_rejects_empty_updates_and_foreign_webhooks(self):
        inbound = PersistentAgentInboundWebhook.objects.create(agent=self.other_agent, name="Foreign inbound")
        outbound = PersistentAgentWebhook.objects.create(
            agent=self.other_agent,
            name="Foreign outbound",
            url="https://example.com/foreign",
        )

        empty_update = execute_manage_inbound_webhooks(
            self.agent,
            {
                "action": "update",
                "webhook_id": str(inbound.id),
                "will_continue_work": True,
            },
        )
        foreign_outbound = execute_manage_outbound_webhooks(
            self.agent,
            {
                "action": "get",
                "webhook_id": str(outbound.id),
                "will_continue_work": True,
            },
        )

        self.assertEqual(empty_update["status"], "error")
        self.assertIn("not found for this agent", empty_update["message"])
        self.assertEqual(foreign_outbound["status"], "error")
        self.assertIn("not found for this agent", foreign_outbound["message"])

        own_inbound = PersistentAgentInboundWebhook.objects.create(agent=self.agent, name="Own inbound")
        own_empty_update = execute_manage_inbound_webhooks(
            self.agent,
            {
                "action": "update",
                "webhook_id": str(own_inbound.id),
                "will_continue_work": True,
            },
        )
        self.assertEqual(own_empty_update["status"], "error")
        self.assertIn("Provide name or is_active", own_empty_update["message"])

    def test_management_rejects_duplicate_names_and_invalid_urls(self):
        first_inbound = execute_manage_inbound_webhooks(
            self.agent,
            {"action": "create", "name": "Duplicate", "will_continue_work": True},
        )
        duplicate_inbound = execute_manage_inbound_webhooks(
            self.agent,
            {"action": "create", "name": "Duplicate", "will_continue_work": True},
        )
        first_outbound = execute_manage_outbound_webhooks(
            self.agent,
            {
                "action": "create",
                "name": "Duplicate",
                "url": "https://example.com/first",
                "will_continue_work": True,
            },
        )
        duplicate_outbound = execute_manage_outbound_webhooks(
            self.agent,
            {
                "action": "create",
                "name": "Duplicate",
                "url": "https://example.com/second",
                "will_continue_work": True,
            },
        )
        invalid_outbound = execute_manage_outbound_webhooks(
            self.agent,
            {
                "action": "create",
                "name": "Invalid URL",
                "url": "not a URL",
                "will_continue_work": False,
            },
        )

        self.assertEqual(first_inbound["status"], "success")
        self.assertEqual(duplicate_inbound["status"], "error")
        self.assertIn("already exists", duplicate_inbound["message"])
        self.assertEqual(first_outbound["status"], "success")
        self.assertEqual(duplicate_outbound["status"], "error")
        self.assertIn("already exists", duplicate_outbound["message"])
        self.assertEqual(invalid_outbound["status"], "error")
        self.assertIn("valid URL", invalid_outbound["message"])
        self.assertTrue(invalid_outbound["auto_sleep_ok"])

    @patch("api.services.agent_webhooks.Analytics.track_event")
    def test_management_analytics_use_agent_source_without_urls(self, mock_track_event):
        with self.captureOnCommitCallbacks(execute=True):
            result = execute_manage_outbound_webhooks(
                self.agent,
                {
                    "action": "create",
                    "name": "Analytics hook",
                    "url": "https://example.com/private?token=secret",
                    "will_continue_work": False,
                },
            )

        self.assertEqual(result["status"], "success")
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["source"], AnalyticsSource.AGENT)
        self.assertNotIn("url", kwargs["properties"])

    def test_webhook_tools_are_hidden_until_system_skill_is_enabled(self):
        webhook_tools = {
            "manage_inbound_webhooks",
            "manage_outbound_webhooks",
            "send_webhook_event",
        }
        PersistentAgentWebhook.objects.create(
            agent=self.agent,
            name="Existing destination",
            url="https://example.com/existing",
        )
        self.assertTrue(webhook_tools.isdisjoint(get_static_tool_names(self.agent)))
        self.assertTrue(webhook_tools.isdisjoint(get_available_builtin_tool_entries(self.agent)))
        self.assertTrue(
            webhook_tools.issubset(get_available_builtin_tool_entries(self.agent, include_hidden=True))
        )

        matches = shortlist_system_skills(
            "Aimfox has an add webhook feature; set it up so events trigger you",
            available_tool_names=get_available_system_skill_tool_names(self.agent),
            discovery_only=True,
        )
        self.assertIn(WEBHOOKS_SYSTEM_SKILL_KEY, {definition.skill_key for definition in matches})

        result = enable_system_skills(self.agent, [WEBHOOKS_SYSTEM_SKILL_KEY])
        self.assertEqual(result["enabled"], [WEBHOOKS_SYSTEM_SKILL_KEY])
        self.assertEqual(result["invalid"], [])
        self.assertEqual(
            set(
                PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list(
                    "tool_full_name",
                    flat=True,
                )
            ),
            webhook_tools,
        )
        enabled_names = {
            definition["function"]["name"]
            for definition in get_enabled_tool_definitions(self.agent)
        }
        self.assertTrue(webhook_tools.issubset(enabled_names))

        inbound_result = execute_enabled_tool(
            self.agent,
            "manage_inbound_webhooks",
            {"action": "list", "will_continue_work": True},
        )
        outbound_result = execute_enabled_tool(
            self.agent,
            "manage_outbound_webhooks",
            {"action": "list", "will_continue_work": True},
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        send_result = execute_enabled_tool(
            self.agent,
            "send_webhook_event",
            {
                "webhook_id": "00000000-0000-0000-0000-000000000000",
                "payload": {},
                "will_continue_work": False,
            },
        )

        self.assertEqual(inbound_result["status"], "success")
        self.assertEqual(outbound_result["status"], "success")
        self.assertIn("Webhook not found", send_result["message"])

    def test_existing_webhook_lazily_enables_skill_unless_previously_disabled(self):
        webhook_tools = {
            "manage_inbound_webhooks",
            "manage_outbound_webhooks",
            "send_webhook_event",
        }
        PersistentAgentWebhook.objects.create(
            agent=self.agent,
            name="Legacy destination",
            url="https://example.com/legacy",
        )

        prompt = format_recent_skills_for_prompt(self.agent)

        self.assertIn("System Skill: Webhooks", prompt)
        self.assertEqual(
            set(
                PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list(
                    "tool_full_name", flat=True
                )
            ),
            webhook_tools,
        )

        PersistentAgentSystemSkillState.objects.filter(
            agent=self.agent,
            skill_key=WEBHOOKS_SYSTEM_SKILL_KEY,
        ).update(is_enabled=False)
        PersistentAgentEnabledTool.objects.filter(agent=self.agent).delete()

        self.assertNotIn("System Skill: Webhooks", format_recent_skills_for_prompt(self.agent))
        self.assertFalse(PersistentAgentEnabledTool.objects.filter(agent=self.agent).exists())

    def test_webhook_tools_are_available_in_planning_mode(self):
        webhook_tools = {
            "manage_inbound_webhooks",
            "manage_outbound_webhooks",
            "send_webhook_event",
        }
        enable_system_skills(self.agent, [WEBHOOKS_SYSTEM_SKILL_KEY])
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        enabled_names = {
            definition["function"]["name"]
            for definition in get_enabled_tool_definitions(self.agent)
        }
        self.assertTrue(webhook_tools.issubset(enabled_names))
        for tool_name in webhook_tools:
            self.assertFalse(planning_mode_disallows_tool(self.agent, tool_name))

    def test_webhook_system_skill_context_distinguishes_directions_without_urls(self):
        inbound = PersistentAgentInboundWebhook.objects.create(agent=self.agent, name="Aimfox inbound")
        outbound = PersistentAgentWebhook.objects.create(
            agent=self.agent,
            name="Operations outbound",
            url="https://example.com/private?token=outbound-secret",
        )
        enable_system_skills(self.agent, [WEBHOOKS_SYSTEM_SKILL_KEY])

        prompt = format_recent_skills_for_prompt(self.agent)

        self.assertIn("System Skill: Webhooks", prompt)
        self.assertIn("Prefer native Gobii webhooks over Pipedream", prompt)
        self.assertIn("Inbound triggers:", prompt)
        self.assertIn(str(inbound.id), prompt)
        self.assertIn("Outbound destinations:", prompt)
        self.assertIn(str(outbound.id), prompt)
        self.assertNotIn(inbound.secret, prompt)
        self.assertNotIn(outbound.url, prompt)
        self.assertNotIn("ask the user to add it on the agent settings page", prompt)

    def test_webhook_eval_suite_is_registered(self):
        import api.evals.loader  # noqa: F401 - registers scenarios and suites
        from api.evals.registry import ScenarioRegistry
        from api.evals.scenarios.webhooks import WEBHOOK_SCENARIO_SLUGS, WEBHOOKS_SUITE_SLUG
        from api.evals.suites import SuiteRegistry

        suite = SuiteRegistry.get(WEBHOOKS_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), WEBHOOK_SCENARIO_SLUGS)
        for scenario_slug in WEBHOOK_SCENARIO_SLUGS:
            self.assertIsNotNone(ScenarioRegistry.get(scenario_slug))


@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
class AgentWebhookConsoleViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="console-owner",
            email="console@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Console Tester",
            charter="Manage webhooks",
            browser_use_agent=cls.browser_agent,
        )
        cls.agent_id = agent.id

    def setUp(self):
        self.user = type(self).user
        self.client.force_login(self.user)
        self.agent = PersistentAgent.objects.get(pk=self.agent_id)

    @tag("batch_agent_webhooks")
    def test_console_creates_webhook(self):
        response = self.client.post(
            reverse("console_agent_settings", args=[self.agent_id]),
            {
                "webhook_action": "create",
                "webhook_name": "CI Hook",
                "webhook_url": "https://example.com/ci",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertTrue(
            PersistentAgentWebhook.objects.filter(agent=self.agent, name="CI Hook").exists()
        )

    @tag("batch_agent_webhooks")
    def test_console_updates_webhook(self):
        webhook = PersistentAgentWebhook.objects.create(
            agent=self.agent,
            name="Original",
            url="https://example.com/old",
        )
        response = self.client.post(
            reverse("console_agent_settings", args=[self.agent_id]),
            {
                "webhook_action": "update",
                "webhook_id": str(webhook.id),
                "webhook_name": "Updated",
                "webhook_url": "https://example.com/new",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        webhook.refresh_from_db()
        self.assertEqual(webhook.name, "Updated")
        self.assertEqual(webhook.url, "https://example.com/new")

    @tag("batch_agent_webhooks")
    def test_console_deletes_webhook(self):
        webhook = PersistentAgentWebhook.objects.create(
            agent=self.agent,
            name="To Delete",
            url="https://example.com/delete",
        )
        response = self.client.post(
            reverse("console_agent_settings", args=[self.agent_id]),
            {
                "webhook_action": "delete",
                "webhook_id": str(webhook.id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertFalse(
            PersistentAgentWebhook.objects.filter(pk=webhook.pk).exists()
        )

    @tag("batch_agent_webhooks")
    def test_console_creates_inbound_webhook(self):
        response = self.client.post(
            reverse("console_agent_settings", args=[self.agent_id]),
            {
                "inbound_webhook_action": "create",
                "inbound_webhook_name": "Build Trigger",
                "inbound_webhook_is_active": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        webhook = PersistentAgentInboundWebhook.objects.get(agent=self.agent, name="Build Trigger")
        self.assertTrue(webhook.is_active)
        self.assertTrue(webhook.secret)

    @tag("batch_agent_webhooks")
    def test_console_updates_inbound_webhook(self):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Inbound Original",
            is_active=True,
        )
        response = self.client.post(
            reverse("console_agent_settings", args=[self.agent_id]),
            {
                "inbound_webhook_action": "update",
                "inbound_webhook_id": str(webhook.id),
                "inbound_webhook_name": "Inbound Updated",
                "inbound_webhook_is_active": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        webhook.refresh_from_db()
        self.assertEqual(webhook.name, "Inbound Updated")
        self.assertFalse(webhook.is_active)

    @tag("batch_agent_webhooks")
    def test_console_rotates_inbound_webhook_secret(self):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Rotate Me",
        )
        old_secret = webhook.secret
        response = self.client.post(
            reverse("console_agent_settings", args=[self.agent_id]),
            {
                "inbound_webhook_action": "rotate_secret",
                "inbound_webhook_id": str(webhook.id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        webhook.refresh_from_db()
        self.assertNotEqual(webhook.secret, old_secret)

    @tag("batch_agent_webhooks")
    def test_console_deletes_inbound_webhook(self):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Inbound Delete",
        )
        response = self.client.post(
            reverse("console_agent_settings", args=[self.agent_id]),
            {
                "inbound_webhook_action": "delete",
                "inbound_webhook_id": str(webhook.id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertFalse(
            PersistentAgentInboundWebhook.objects.filter(pk=webhook.pk).exists()
        )

    @tag("batch_agent_webhooks")
    @patch("console.agent_settings.service.Analytics.track_event")
    def test_inbound_webhook_actions_emit_analytics(self, mock_track_event):
        settings_url = reverse("console_agent_settings", args=[self.agent_id])

        with self.captureOnCommitCallbacks(execute=True):
            create_response = self.client.post(
                settings_url,
                {
                    "inbound_webhook_action": "create",
                    "inbound_webhook_name": "Build Trigger",
                    "inbound_webhook_is_active": "true",
                },
            )

        self.assertEqual(create_response.status_code, 200)
        self.assertTrue(create_response.json()["success"])
        webhook = PersistentAgentInboundWebhook.objects.get(agent=self.agent, name="Build Trigger")

        with self.captureOnCommitCallbacks(execute=True):
            update_response = self.client.post(
                settings_url,
                {
                    "inbound_webhook_action": "update",
                    "inbound_webhook_id": str(webhook.id),
                    "inbound_webhook_name": "Build Trigger Updated",
                    "inbound_webhook_is_active": "false",
                },
            )

        self.assertEqual(update_response.status_code, 200)
        self.assertTrue(update_response.json()["success"])
        webhook.refresh_from_db()

        with self.captureOnCommitCallbacks(execute=True):
            rotate_response = self.client.post(
                settings_url,
                {
                    "inbound_webhook_action": "rotate_secret",
                    "inbound_webhook_id": str(webhook.id),
                },
            )

        self.assertEqual(rotate_response.status_code, 200)
        self.assertTrue(rotate_response.json()["success"])

        with self.captureOnCommitCallbacks(execute=True):
            delete_response = self.client.post(
                settings_url,
                {
                    "inbound_webhook_action": "delete",
                    "inbound_webhook_id": str(webhook.id),
                },
            )

        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["success"])

        self.assertEqual(
            [call.kwargs["event"] for call in mock_track_event.call_args_list],
            [
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_ADDED,
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_UPDATED,
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_SECRET_ROTATED,
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_DELETED,
            ],
        )
        create_props = mock_track_event.call_args_list[0].kwargs["properties"]
        self.assertEqual(create_props["agent_id"], str(self.agent.id))
        self.assertEqual(create_props["webhook_id"], str(webhook.id))
        self.assertEqual(create_props["webhook_name"], "Build Trigger")
        self.assertTrue(create_props["is_active"])

        update_props = mock_track_event.call_args_list[1].kwargs["properties"]
        self.assertEqual(update_props["webhook_name"], "Build Trigger Updated")
        self.assertFalse(update_props["is_active"])


class InboundAgentWebhookEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="inbound-owner",
            email="inbound@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=cls.user,
            email=cls.user.email,
            verified=True,
            primary=True,
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Inbound Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Inbound Receiver",
            charter="Receive inbound webhook events",
            browser_use_agent=cls.browser_agent,
        )
        cls.webhook = PersistentAgentInboundWebhook.objects.create(
            agent=cls.agent,
            name="Deploy Hook",
        )

    @tag("batch_agent_webhooks")
    @patch("api.webhooks.Analytics.track_event")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_emits_analytics(self, mock_delay, mock_track_event):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"ok","build_id":42}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        mock_delay.assert_called_once_with(str(self.agent.id))
        mock_track_event.assert_called_once()
        self.assertEqual(
            mock_track_event.call_args.kwargs["event"],
            AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_TRIGGERED,
        )
        props = mock_track_event.call_args.kwargs["properties"]
        self.assertEqual(props["agent_id"], str(self.agent.id))
        self.assertEqual(props["webhook_id"], str(self.webhook.id))
        self.assertEqual(props["webhook_name"], self.webhook.name)
        self.assertEqual(props["payload_kind"], "json")
        self.assertEqual(props["attachment_count"], 0)
        self.assertTrue(props["message_id"])

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_accepts_json_payload(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"ok","build_id":42}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["webhookId"], str(self.webhook.id))

        message = PersistentAgentMessage.objects.get(id=payload["messageId"])
        self.assertEqual(message.owner_agent_id, self.agent.id)
        self.assertEqual(message.conversation.channel, "other")
        self.assertEqual(message.conversation.display_name, self.webhook.name)
        self.assertEqual(message.raw_payload["source_kind"], "webhook")
        self.assertEqual(message.raw_payload["webhook_name"], self.webhook.name)
        self.assertEqual(message.raw_payload["payload_kind"], "json")
        self.assertEqual(message.body, json.dumps({"build_id": 42, "status": "ok"}, indent=2, sort_keys=True))
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_accepts_multipart_payload_and_attachments(self, mock_delay):
        upload = SimpleUploadedFile("deploy.json", b'{"ok": true}', content_type="application/json")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data={
                    "environment": "prod",
                    "build_id": "123",
                    "artifact": upload,
                },
            )

        self.assertEqual(response.status_code, 202, response.content)
        message = PersistentAgentMessage.objects.get(id=response.json()["messageId"])
        self.assertEqual(message.attachments.count(), 1)
        self.assertEqual(message.raw_payload["payload_kind"], "form")
        self.assertEqual(
            message.body,
            json.dumps({"build_id": "123", "environment": "prod"}, indent=2, sort_keys=True),
        )
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    def test_inbound_webhook_rejects_invalid_secret(self):
        response = self.client.post(
            f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t=wrong-secret",
            data='{"status":"ok"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_rejects_agent_from_other_environment(self, mock_delay):
        with patch("api.webhooks.settings.GOBII_RELEASE_ENV", "other-env"):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"ok"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=self.agent).exists())
        mock_delay.assert_not_called()

    @tag("batch_agent_webhooks")
    def test_inbound_webhook_rejects_inactive_webhook(self):
        self.webhook.is_active = False
        self.webhook.save(update_fields=["is_active"])

        response = self.client.post(
            f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
            data='{"status":"ok"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)

    @tag("batch_agent_webhooks")
    @patch("api.agent.comms.message_service.send_billing_pause_auto_reply")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_skips_processing_when_owner_billing_paused(self, mock_delay, mock_auto_reply):
        billing = self.user.billing
        billing.execution_paused = True
        billing.execution_pause_reason = "billing_delinquency"
        billing.execution_paused_at = timezone.now()
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"paused"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertTrue(PersistentAgentMessage.objects.filter(id=payload["messageId"]).exists())
        mock_delay.assert_not_called()
        mock_auto_reply.assert_not_called()

        self.webhook.refresh_from_db()
        self.assertIsNotNone(self.webhook.last_triggered_at)

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_rotated_secret_invalidates_previous_url(self, mock_delay):
        old_secret = self.webhook.secret
        self.webhook.rotate_secret()
        self.webhook.refresh_from_db()

        old_response = self.client.post(
            f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={old_secret}",
            data='{"status":"stale"}',
            content_type="application/json",
        )
        self.assertEqual(old_response.status_code, 403)

        with self.captureOnCommitCallbacks(execute=True):
            new_response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"fresh"}',
                content_type="application/json",
            )
        self.assertEqual(new_response.status_code, 202, new_response.content)
        mock_delay.assert_called_once_with(str(self.agent.id))


class InboundAgentWebhookParsingTests(TestCase):
    @tag("batch_agent_webhooks")
    def test_parse_multipart_request_does_not_access_raw_body(self):
        upload = SimpleUploadedFile("deploy.json", b'{"ok": true}', content_type="application/json")
        post_data = QueryDict("", mutable=True)
        post_data["environment"] = "prod"
        post_data["build_id"] = "123"

        class MultipartRequest:
            content_type = "multipart/form-data; boundary=test-boundary"
            encoding = "utf-8"
            method = "POST"
            path = "/api/webhooks/inbound/test/"
            POST = post_data
            FILES = MultiValueDict({"artifact": [upload]})
            GET = QueryDict("t=secret&source=ci")

            @property
            def body(self):
                raise AssertionError("multipart webhook parsing should not read request.body")

        body, raw_payload, attachments = _parse_inbound_agent_webhook_request(MultipartRequest())

        self.assertEqual(
            body,
            json.dumps({"build_id": "123", "environment": "prod"}, indent=2, sort_keys=True),
        )
        self.assertEqual(raw_payload["payload_kind"], "form")
        self.assertEqual(raw_payload["query_params"], {"source": "ci"})
        self.assertEqual(raw_payload["attachments"][0]["filename"], "deploy.json")
        self.assertEqual(attachments, [upload])
