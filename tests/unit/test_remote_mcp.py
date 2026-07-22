import base64
import json
import uuid
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    EvalRun,
    EvalRunTask,
    IntelligenceTier,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentCompletion,
    PersistentAgentError,
    PersistentAgentMessage,
    PersistentAgentPromptArchive,
    PersistentAgentStep,
    PersistentAgentToolCall,
    UserQuota,
    build_web_agent_address,
    build_web_user_address,
)
from api.models import DEFAULT_INTELLIGENCE_TIER_KEY
from api.models import ApiKey


User = get_user_model()


@tag("batch_mcp_tools")
class RemoteMCPViewTests(TestCase):
    def setUp(self):
        self.tier, _ = IntelligenceTier.objects.update_or_create(
            key=DEFAULT_INTELLIGENCE_TIER_KEY,
            defaults={
                "display_name": "Standard",
                "rank": 10,
                "credit_multiplier": "1.00",
                "is_default": True,
            },
        )
        self.premium_tier, _ = IntelligenceTier.objects.update_or_create(
            key="premium",
            defaults={
                "display_name": "Premium",
                "rank": 20,
                "credit_multiplier": "2.00",
                "is_default": False,
            },
        )
        self.user = User.objects.create_user(
            username="mcp-user@example.com",
            email="mcp-user@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 20
        quota.save(update_fields=["agent_limit"])
        self.raw_api_key, self.api_key = ApiKey.create_for_user(self.user, name="mcp")
        self.other_user = User.objects.create_user(
            username="mcp-other@example.com",
            email="mcp-other@example.com",
            password="password",
        )
        UserQuota.objects.get_or_create(user=self.other_user, defaults={"agent_limit": 20})
        self.staff_user = User.objects.create_user(
            username="mcp-staff@example.com",
            email="mcp-staff@example.com",
            password="password",
            is_staff=True,
        )
        UserQuota.objects.get_or_create(user=self.staff_user, defaults={"agent_limit": 20})
        self.raw_staff_api_key, self.staff_api_key = ApiKey.create_for_user(self.staff_user, name="mcp-staff")

    def _post_mcp(self, method, params=None, *, auth="x-api-key", extra_headers=None, api_key=None):
        payload = {
            "jsonrpc": "2.0",
            "id": "test-1",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        headers = dict(extra_headers or {})
        raw_api_key = api_key or self.raw_api_key
        if auth == "x-api-key":
            headers["HTTP_X_API_KEY"] = raw_api_key
        elif auth == "bearer":
            headers["HTTP_AUTHORIZATION"] = f"Bearer {raw_api_key}"
        return self.client.post(
            "/api/v1/mcp/",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def _call_tool(self, name, arguments=None, *, auth="x-api-key", api_key=None):
        return self._post_mcp(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            auth=auth,
            api_key=api_key,
        )

    def _create_agent(self, user, name, *, organization=None):
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=user, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=user,
            organization=organization,
            name=name,
            charter=f"{name} charter",
            browser_use_agent=browser_agent,
            preferred_llm_tier=self.tier,
        )

    def _create_web_message(self, agent, body, *, is_outbound=False):
        agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=build_web_agent_address(agent.id),
            defaults={"owner_agent": agent},
        )
        if agent_endpoint.owner_agent_id != agent.id:
            agent_endpoint.owner_agent = agent
            agent_endpoint.save(update_fields=["owner_agent"])
        user_address = build_web_user_address(agent.user_id, agent.id)
        user_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=user_address,
        )
        conversation, _ = PersistentAgentConversation.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=user_address,
            defaults={"owner_agent": agent, "display_name": agent.user.email},
        )
        if conversation.owner_agent_id != agent.id:
            conversation.owner_agent = agent
            conversation.save(update_fields=["owner_agent"])

        return PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=agent_endpoint if is_outbound else user_endpoint,
            to_endpoint=user_endpoint if is_outbound else None,
            conversation=conversation,
            is_outbound=is_outbound,
            body=body,
            raw_payload={"source": "unit_test"},
        )

    def _structured_content(self, response):
        return response.json()["result"]["structuredContent"]

    def test_requires_api_key(self):
        response = self.client.post(
            "/api/v1/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": "test-1", "method": "initialize"}),
            content_type="application/json",
        )

        self.assertIn(response.status_code, [401, 403])

    def test_initialize_accepts_existing_api_key_headers(self):
        response = self._post_mcp("initialize")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tools", payload["result"]["capabilities"])

        bearer_response = self._post_mcp("initialize", auth="bearer")
        self.assertEqual(bearer_response.status_code, 200)

    def test_list_tools_and_scope_agent_listing(self):
        agent = self._create_agent(self.user, "MCP Owned")
        self._create_agent(self.other_user, "MCP Other")

        tools_response = self._post_mcp("tools/list")
        self.assertEqual(tools_response.status_code, 200)
        tool_names = {tool["name"] for tool in tools_response.json()["result"]["tools"]}
        self.assertIn("gobii_list_agents", tool_names)
        self.assertIn("gobii_get_agent_config_options", tool_names)
        self.assertIn("gobii_send_agent_message", tool_names)
        self.assertIn("gobii_wait_for_agent_event", tool_names)
        self.assertIn("gobii_get_agent_debug_trace", tool_names)
        self.assertIn("gobii_upload_agent_file", tool_names)

        debug_tool = next(tool for tool in tools_response.json()["result"]["tools"] if tool["name"] == "gobii_get_agent_debug_trace")
        self.assertIn("sanitized", debug_tool["description"])
        self.assertEqual(debug_tool["inputSchema"]["properties"]["limit"]["maximum"], 50)
        self.assertIn("audit_events", debug_tool["inputSchema"]["properties"]["include"]["items"]["enum"])
        self.assertIn("user_id", debug_tool["inputSchema"]["properties"])
        self.assertIn("organization_id", debug_tool["inputSchema"]["properties"])

        list_response = self._call_tool("gobii_list_agents")
        self.assertEqual(list_response.status_code, 200)
        content = self._structured_content(list_response)
        self.assertEqual(content["total"], 1)
        self.assertEqual(content["agents"][0]["id"], str(agent.id))
        self.assertNotIn("access", content)

    def test_non_admin_scope_params_return_structured_tool_error(self):
        other_agent = self._create_agent(self.other_user, "Scoped Other Agent")

        list_response = self._call_tool(
            "gobii_list_agents",
            {"user_id": self.other_user.id},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertTrue(list_response.json()["result"]["isError"])
        list_content = self._structured_content(list_response)
        self.assertEqual(list_content["status"], "error")
        self.assertEqual(list_content["details"]["code"], "admin_scope_required")
        self.assertIn("user_id", list_content["details"]["fields"])

        get_response = self._call_tool(
            "gobii_get_agent",
            {"agent_id": str(other_agent.id), "user_id": self.other_user.id},
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertTrue(get_response.json()["result"]["isError"])
        self.assertEqual(self._structured_content(get_response)["details"]["code"], "admin_scope_required")

    def test_staff_can_cross_account_get_debug_and_timeline_by_agent_id(self):
        other_agent = self._create_agent(self.other_user, "Staff Cross Account Agent")
        self._create_web_message(
            other_agent,
            "Investigate this. Authorization: Bearer staff-cross-account-secret",
        )

        get_response = self._call_tool(
            "gobii_get_agent",
            {"agent_id": str(other_agent.id)},
            api_key=self.raw_staff_api_key,
        )
        self.assertEqual(get_response.status_code, 200)
        get_content = self._structured_content(get_response)
        self.assertFalse(get_response.json()["result"]["isError"])
        self.assertEqual(get_content["agent"]["id"], str(other_agent.id))
        self.assertEqual(get_content["access"]["admin_access"], True)
        self.assertEqual(get_content["access"]["access_scope"], "staff_cross_account")
        self.assertEqual(get_content["access"]["target_user_id"], str(self.other_user.id))

        timeline_response = self._call_tool(
            "gobii_get_agent_timeline",
            {"agent_id": str(other_agent.id), "limit": 5},
            api_key=self.raw_staff_api_key,
        )
        self.assertEqual(timeline_response.status_code, 200)
        timeline_content = self._structured_content(timeline_response)
        self.assertFalse(timeline_response.json()["result"]["isError"])
        self.assertEqual(timeline_content["access"]["target_user_id"], str(self.other_user.id))

        debug_response = self._call_tool(
            "gobii_get_agent_debug_trace",
            {
                "agent_id": str(other_agent.id),
                "limit": 5,
                "include": ["audit_events"],
            },
            api_key=self.raw_staff_api_key,
        )
        self.assertEqual(debug_response.status_code, 200)
        debug_content = self._structured_content(debug_response)
        self.assertFalse(debug_response.json()["result"]["isError"])
        self.assertEqual(debug_content["agent"]["id"], str(other_agent.id))
        self.assertEqual(debug_content["access"]["target_user_id"], str(self.other_user.id))
        serialized = json.dumps(debug_content)
        self.assertNotIn("staff-cross-account-secret", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_staff_can_use_scoped_list_agents(self):
        self._create_agent(self.user, "Default User Agent")
        other_agent = self._create_agent(self.other_user, "Scoped Listed Agent")

        response = self._call_tool(
            "gobii_list_agents",
            {"user_id": self.other_user.id},
            api_key=self.raw_staff_api_key,
        )

        self.assertEqual(response.status_code, 200)
        content = self._structured_content(response)
        self.assertFalse(response.json()["result"]["isError"])
        self.assertEqual(content["total"], 1)
        self.assertEqual(content["agents"][0]["id"], str(other_agent.id))
        self.assertEqual(content["access"]["admin_access"], True)
        self.assertEqual(content["access"]["requested_user_id"], str(self.other_user.id))

    def test_staff_can_create_agent_in_scoped_user_and_org_contexts(self):
        org = Organization.objects.create(name="Scoped MCP Org", slug="scoped-mcp-org", created_by=self.other_user)
        org.billing.purchased_seats = 1
        org.billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.other_user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        with (
            patch.object(BrowserUseAgent, "select_random_proxy", return_value=None),
            patch("api.services.persistent_agents.maybe_schedule_short_description"),
            patch("api.services.persistent_agents.maybe_schedule_mini_description"),
            patch("api.services.persistent_agents.maybe_schedule_agent_tags"),
            patch("api.services.persistent_agents.maybe_schedule_agent_avatar"),
            patch("api.agent.tasks.enqueue_interactive_process_agent_events"),
            patch("api.serializers.can_user_use_personal_agents_and_api", return_value=True),
            self.captureOnCommitCallbacks(execute=True),
        ):
            user_response = self._call_tool(
                "gobii_create_agent",
                {
                    "name": "Scoped User Created Agent",
                    "charter": "Created for another user by staff.",
                    "user_id": self.other_user.id,
                },
                api_key=self.raw_staff_api_key,
            )
            org_response = self._call_tool(
                "gobii_create_agent",
                {
                    "name": "Scoped Org Created Agent",
                    "charter": "Created for another organization by staff.",
                    "organization_id": str(org.id),
                },
                api_key=self.raw_staff_api_key,
            )

        self.assertEqual(user_response.status_code, 200)
        user_content = self._structured_content(user_response)
        self.assertFalse(user_response.json()["result"]["isError"], user_response.content)
        user_agent = PersistentAgent.objects.get(id=user_content["agent"]["id"])
        self.assertEqual(user_agent.user_id, self.other_user.id)
        self.assertIsNone(user_agent.organization_id)
        self.assertEqual(user_content["access"]["requested_user_id"], str(self.other_user.id))

        self.assertEqual(org_response.status_code, 200)
        org_content = self._structured_content(org_response)
        self.assertFalse(org_response.json()["result"]["isError"], org_response.content)
        org_agent = PersistentAgent.objects.get(id=org_content["agent"]["id"])
        self.assertEqual(org_agent.organization_id, org.id)
        self.assertEqual(org_agent.user_id, self.other_user.id)
        self.assertEqual(org_content["access"]["requested_organization_id"], str(org.id))

    def test_staff_cross_account_update_agent_records_access_metadata(self):
        other_agent = self._create_agent(self.other_user, "Staff Mutated Agent")

        response = self._call_tool(
            "gobii_update_agent",
            {
                "agent_id": str(other_agent.id),
                "user_id": self.other_user.id,
                "charter": "Updated by staff through scoped Remote MCP.",
            },
            api_key=self.raw_staff_api_key,
        )

        self.assertEqual(response.status_code, 200)
        content = self._structured_content(response)
        self.assertFalse(response.json()["result"]["isError"])
        other_agent.refresh_from_db()
        self.assertEqual(other_agent.charter, "Updated by staff through scoped Remote MCP.")
        self.assertEqual(content["access"]["admin_access"], True)
        self.assertEqual(content["access"]["target_user_id"], str(self.other_user.id))

    def test_staff_scope_invalid_targets_return_structured_tool_errors(self):
        missing_user_response = self._call_tool(
            "gobii_list_agents",
            {"user_id": 999999},
            api_key=self.raw_staff_api_key,
        )
        self.assertEqual(missing_user_response.status_code, 200)
        self.assertTrue(missing_user_response.json()["result"]["isError"])
        self.assertEqual(self._structured_content(missing_user_response)["details"]["code"], "user_not_found")

        missing_org_response = self._call_tool(
            "gobii_list_agents",
            {"organization_id": str(uuid.uuid4())},
            api_key=self.raw_staff_api_key,
        )
        self.assertEqual(missing_org_response.status_code, 200)
        self.assertTrue(missing_org_response.json()["result"]["isError"])
        self.assertEqual(self._structured_content(missing_org_response)["details"]["code"], "organization_not_found")

        missing_agent_response = self._call_tool(
            "gobii_get_agent",
            {"agent_id": str(uuid.uuid4())},
            api_key=self.raw_staff_api_key,
        )
        self.assertEqual(missing_agent_response.status_code, 200)
        self.assertTrue(missing_agent_response.json()["result"]["isError"])
        self.assertEqual(
            self._structured_content(missing_agent_response)["details"]["code"],
            "agent_not_found_or_inaccessible",
        )

    def test_lifecycle_tools_create_update_link_and_archive_agent(self):
        existing_agent = self._create_agent(self.user, "Existing MCP Agent")

        with (
            patch.object(BrowserUseAgent, "select_random_proxy", return_value=None),
            patch("api.services.persistent_agents.maybe_schedule_short_description"),
            patch("api.services.persistent_agents.maybe_schedule_mini_description"),
            patch("api.services.persistent_agents.maybe_schedule_agent_tags"),
            patch("api.services.persistent_agents.maybe_schedule_agent_avatar"),
            patch("api.agent.tasks.process_agent_events_task.delay"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            create_response = self._call_tool(
                "gobii_create_agent",
                {
                    "name": "Remote MCP Agent",
                    "charter": "Coordinate MCP work.",
                    "is_active": True,
                },
            )

        self.assertEqual(create_response.status_code, 200)
        created_agent_id = self._structured_content(create_response)["agent"]["id"]
        created_agent = PersistentAgent.objects.get(id=created_agent_id)
        self.assertIsNone(created_agent.schedule)

        update_response = self._call_tool(
            "gobii_update_agent",
            {
                "agent_id": created_agent_id,
                "charter": "Updated through MCP.",
                "preferred_llm_tier": "premium",
                "daily_credit_limit": 7,
            },
        )
        self.assertEqual(update_response.status_code, 200)
        created_agent.refresh_from_db()
        self.assertEqual(created_agent.charter, "Updated through MCP.")
        self.assertEqual(created_agent.preferred_llm_tier, self.premium_tier)
        self.assertEqual(created_agent.daily_credit_limit, 7)

        config_response = self._call_tool(
            "gobii_get_agent_config_options",
            {"agent_id": created_agent_id},
        )
        self.assertEqual(config_response.status_code, 200)
        config_content = self._structured_content(config_response)
        self.assertIn("preferred_llm_tier", config_content["fields"])
        self.assertIn("daily_credit_limit", config_content["fields"])
        tier_keys = {
            option["key"]
            for option in config_content["fields"]["preferred_llm_tier"]["options"]
        }
        self.assertIn("standard", tier_keys)
        self.assertIn("premium", tier_keys)

        link_response = self._call_tool(
            "gobii_link_agents",
            {
                "agent_id": str(existing_agent.id),
                "peer_agent_id": created_agent_id,
                "messages_per_window": 12,
                "window_hours": 4,
            },
        )
        self.assertEqual(link_response.status_code, 200)
        link_content = self._structured_content(link_response)
        self.assertTrue(link_content["created"])
        self.assertTrue(AgentPeerLink.objects.filter(id=link_content["link"]["id"]).exists())

        list_links_response = self._call_tool(
            "gobii_list_agent_links",
            {"agent_id": str(existing_agent.id)},
        )
        self.assertEqual(list_links_response.status_code, 200)
        self.assertEqual(len(self._structured_content(list_links_response)["links"]), 1)

        unlink_response = self._call_tool(
            "gobii_unlink_agents",
            {"peer_link_id": link_content["link"]["id"]},
        )
        self.assertEqual(unlink_response.status_code, 200)
        self.assertFalse(AgentPeerLink.objects.filter(id=link_content["link"]["id"]).exists())

        archive_response = self._call_tool("gobii_archive_agent", {"agent_id": created_agent_id})
        self.assertEqual(archive_response.status_code, 200)
        created_agent.refresh_from_db()
        self.assertTrue(created_agent.is_deleted)

    def test_update_unscheduled_agent_config_without_schedule(self):
        agent = self._create_agent(self.user, "Unscheduled Config Update")
        self.assertIsNone(agent.schedule)

        response = self._call_tool(
            "gobii_update_agent",
            {
                "agent_id": str(agent.id),
                "preferred_llm_tier": "premium",
                "daily_credit_limit": 11,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"]["isError"])
        agent.refresh_from_db()
        self.assertIsNone(agent.schedule)
        self.assertEqual(agent.preferred_llm_tier, self.premium_tier)
        self.assertEqual(agent.daily_credit_limit, 11)

    def test_update_unscheduled_agent_with_explicit_null_schedule(self):
        agent = self._create_agent(self.user, "Explicit Null Schedule Update")
        PersistentAgent.objects.filter(id=agent.id).update(schedule="@daily")

        response = self._call_tool(
            "gobii_update_agent",
            {
                "agent_id": str(agent.id),
                "schedule": None,
                "preferred_llm_tier": "premium",
                "daily_credit_limit": 13,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"]["isError"])
        agent.refresh_from_db()
        self.assertIsNone(agent.schedule)
        self.assertEqual(agent.preferred_llm_tier, self.premium_tier)
        self.assertEqual(agent.daily_credit_limit, 13)

    def test_update_agent_invalid_schedule_returns_structured_tool_error(self):
        agent = self._create_agent(self.user, "Invalid Schedule Update")

        response = self._call_tool(
            "gobii_update_agent",
            {
                "agent_id": str(agent.id),
                "schedule": "not a cron schedule",
            },
        )

        self.assertEqual(response.status_code, 200)
        content = self._structured_content(response)
        self.assertTrue(response.json()["result"]["isError"])
        self.assertEqual(content["status"], "error")
        self.assertIn("details", content)
        self.assertIn("schedule", content["details"])

        list_response = self._call_tool("gobii_list_agents")
        self.assertEqual(list_response.status_code, 200)
        self.assertFalse(list_response.json()["result"]["isError"])

    def test_update_agent_omitted_schedule_does_not_revalidate_existing_schedule(self):
        agent = self._create_agent(self.user, "Omitted Schedule Update")
        PersistentAgent.objects.filter(id=agent.id).update(schedule="0 9 * * *")

        with patch(
            "api.agent.core.schedule_parser.ScheduleParser.parse",
            side_effect=AssertionError("schedule was revalidated"),
        ):
            response = self._call_tool(
                "gobii_update_agent",
                {
                    "agent_id": str(agent.id),
                    "preferred_llm_tier": "premium",
                    "daily_credit_limit": 17,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"]["isError"])
        agent.refresh_from_db()
        self.assertEqual(agent.schedule, "0 9 * * *")
        self.assertEqual(agent.preferred_llm_tier, self.premium_tier)
        self.assertEqual(agent.daily_credit_limit, 17)

    def test_create_agent_accepts_null_schedule_and_returns_structured_validation_errors(self):
        with (
            patch.object(BrowserUseAgent, "select_random_proxy", return_value=None),
            patch("api.services.persistent_agents.maybe_schedule_short_description"),
            patch("api.services.persistent_agents.maybe_schedule_mini_description"),
            patch("api.services.persistent_agents.maybe_schedule_agent_tags"),
            patch("api.services.persistent_agents.maybe_schedule_agent_avatar"),
            patch("api.agent.tasks.process_agent_events_task.delay"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            create_response = self._call_tool(
                "gobii_create_agent",
                {
                    "name": "Remote MCP Null Schedule",
                    "charter": "No schedule should be accepted.",
                    "schedule": None,
                },
            )

        self.assertEqual(create_response.status_code, 200)
        created_agent_id = self._structured_content(create_response)["agent"]["id"]
        created_agent = PersistentAgent.objects.get(id=created_agent_id)
        self.assertIsNone(created_agent.schedule)

        invalid_response = self._call_tool(
            "gobii_create_agent",
            {
                "name": "Remote MCP Invalid Schedule",
                "charter": "Invalid schedule should be a tool error.",
                "schedule": "not a cron schedule",
            },
        )
        self.assertEqual(invalid_response.status_code, 200)
        invalid_content = self._structured_content(invalid_response)
        self.assertTrue(invalid_response.json()["result"]["isError"])
        self.assertEqual(invalid_content["status"], "error")
        self.assertIn("details", invalid_content)
        self.assertIn("schedule", invalid_content["details"])

        list_response = self._call_tool("gobii_list_agents")
        self.assertEqual(list_response.status_code, 200)
        self.assertFalse(list_response.json()["result"]["isError"])

    def test_unsupported_tool_arguments_are_structured_errors(self):
        response = self._call_tool(
            "gobii_create_agent",
            {"name": "Unsupported Field", "runtime_session": "fake"},
        )

        self.assertEqual(response.status_code, 200)
        content = self._structured_content(response)
        self.assertTrue(response.json()["result"]["isError"])
        self.assertIn("runtime_session", content["details"]["unsupported_fields"])

        credit_response = self._call_tool(
            "gobii_create_agent",
            {"name": "Bad Credit Limit", "daily_credit_limit": 7.5},
        )
        self.assertEqual(credit_response.status_code, 200)
        credit_content = self._structured_content(credit_response)
        self.assertTrue(credit_response.json()["result"]["isError"])
        self.assertEqual(credit_content["details"]["field"], "daily_credit_limit")

    @patch("api.services.remote_mcp.can_user_use_personal_agents_and_api", return_value=True)
    @patch("api.auth.can_user_use_personal_agents_and_api", return_value=True)
    def test_file_upload_and_message_attachment_path(self, _mock_auth_access, _mock_scope_access):
        agent = self._create_agent(self.user, "File MCP Agent")
        encoded = base64.b64encode(b"hello from mcp").decode("ascii")

        upload_response = self._call_tool(
            "gobii_upload_agent_file",
            {
                "agent_id": str(agent.id),
                "path": "/uploads/hello.txt",
                "content_base64": encoded,
                "mime_type": "text/plain",
            },
        )
        self.assertEqual(upload_response.status_code, 200)
        upload_content = self._structured_content(upload_response)
        self.assertEqual(upload_content["path"], "/uploads/hello.txt")
        node_id = upload_content["node_id"]

        files_response = self._call_tool("gobii_list_agent_files", {"agent_id": str(agent.id)})
        self.assertEqual(files_response.status_code, 200)
        paths = {node["path"] for node in self._structured_content(files_response)["nodes"]}
        self.assertIn("/uploads/hello.txt", paths)

        with (
            patch("api.agent.tasks.process_agent_events_task.delay") as process_delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            send_response = self._call_tool(
                "gobii_send_agent_message",
                {
                    "agent_id": str(agent.id),
                    "body": "Please inspect the attached file.",
                    "attachment_file_paths": ["/uploads/hello.txt"],
                },
            )

        self.assertEqual(send_response.status_code, 200)
        send_content = self._structured_content(send_response)
        message_id = send_content["message_id"]
        self.assertEqual(send_content["agent_id"], str(agent.id))
        self.assertTrue(send_content["cursor"])
        self.assertEqual(send_content["latest_cursor"], send_content["cursor"])
        self.assertEqual(send_content["actor"], {"type": "external", "source": "remote_mcp"})
        timeline_message = send_content["timeline_event"]["message"]
        self.assertEqual(timeline_message["channel"], "mcp")
        self.assertEqual(timeline_message["sourceKind"], "mcp")
        self.assertEqual(timeline_message["sourceLabel"], "Gobii MCP")
        self.assertEqual(timeline_message["senderName"], "Gobii MCP")
        self.assertIsNone(timeline_message["senderUserId"])
        self.assertIsNone(timeline_message["senderAddress"])
        message = PersistentAgentMessage.objects.get(id=message_id)
        self.assertEqual(
            message.raw_payload,
            {
                "source": "remote_mcp",
                "sender_user_id": self.user.id,
                "source_kind": "mcp",
                "source_label": "Gobii MCP",
            },
        )
        attachment = message.attachments.get()
        self.assertEqual(str(attachment.filespace_node_id), node_id)
        process_delay.assert_called_once_with(str(agent.id))

    @patch("api.services.remote_mcp.can_user_use_personal_agents_and_api", return_value=True)
    @patch("api.auth.can_user_use_personal_agents_and_api", return_value=True)
    def test_timeline_cursor_reads_and_wait_filters(self, _mock_auth_access, _mock_scope_access):
        agent = self._create_agent(self.user, "Timeline MCP Agent")

        initial_response = self._call_tool("gobii_get_agent_timeline", {"agent_id": str(agent.id), "limit": 5})
        self.assertEqual(initial_response.status_code, 200)
        initial_cursor = self._structured_content(initial_response)["latest_cursor"]

        timeout_response = self._call_tool(
            "gobii_wait_for_agent_event",
            {
                "agent_id": str(agent.id),
                "after_cursor": initial_cursor,
                "timeout_seconds": 0,
                "event_types": ["message"],
            },
        )
        self.assertEqual(timeout_response.status_code, 200)
        timeout_content = self._structured_content(timeout_response)
        self.assertFalse(timeout_content["matched"])
        self.assertTrue(timeout_content["timed_out"])

        with (
            patch("api.agent.tasks.process_agent_events_task.delay"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            send_response = self._call_tool(
                "gobii_send_agent_message",
                {
                    "agent_id": str(agent.id),
                    "body": "Cursor-visible message.",
                    "trigger_processing": False,
                },
            )
        self.assertEqual(send_response.status_code, 200)
        send_content = self._structured_content(send_response)

        newer_response = self._call_tool(
            "gobii_get_agent_timeline",
            {"agent_id": str(agent.id), "after_cursor": initial_cursor, "limit": 5},
        )
        self.assertEqual(newer_response.status_code, 200)
        newer_content = self._structured_content(newer_response)
        self.assertEqual(newer_content["events"][-1]["message"]["id"], send_content["message_id"])

        wait_response = self._call_tool(
            "gobii_wait_for_agent_event",
            {
                "agent_id": str(agent.id),
                "after_cursor": initial_cursor,
                "timeout_seconds": 0,
                "event_types": ["message"],
                "filters": {
                    "from_actor_type": "external",
                    "to_agent_id": str(agent.id),
                    "message_id": send_content["message_id"],
                    "channel": "mcp",
                },
            },
        )
        self.assertEqual(wait_response.status_code, 200)
        wait_content = self._structured_content(wait_response)
        self.assertTrue(wait_content["matched"])
        self.assertFalse(wait_content["timed_out"])
        self.assertEqual(wait_content["events"][0]["message"]["id"], send_content["message_id"])

        unsupported_filter_response = self._call_tool(
            "gobii_wait_for_agent_event",
            {
                "agent_id": str(agent.id),
                "after_cursor": initial_cursor,
                "timeout_seconds": 0,
                "filters": {"correlation_id": "unsupported"},
            },
        )
        self.assertEqual(unsupported_filter_response.status_code, 200)
        self.assertTrue(unsupported_filter_response.json()["result"]["isError"])

    def test_wait_after_cursor_is_strict_for_message_id_filter(self):
        agent = self._create_agent(self.user, "Strict Cursor MCP Agent")

        with (
            patch("api.agent.tasks.process_agent_events_task.delay"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            send_response = self._call_tool(
                "gobii_send_agent_message",
                {
                    "agent_id": str(agent.id),
                    "body": "This message owns the cursor.",
                    "trigger_processing": False,
                },
            )
        self.assertEqual(send_response.status_code, 200)
        send_content = self._structured_content(send_response)

        wait_response = self._call_tool(
            "gobii_wait_for_agent_event",
            {
                "agent_id": str(agent.id),
                "after_cursor": send_content["cursor"],
                "timeout_seconds": 0,
                "event_types": ["message"],
                "filters": {"message_id": send_content["message_id"]},
            },
        )

        self.assertEqual(wait_response.status_code, 200)
        wait_content = self._structured_content(wait_response)
        self.assertFalse(wait_content["matched"])
        self.assertTrue(wait_content["timed_out"])
        self.assertEqual(wait_content["events"], [])

    def test_wait_matches_agent_owned_outbound_message_filters(self):
        agent = self._create_agent(self.user, "Outbound Wait MCP Agent")

        initial_response = self._call_tool("gobii_get_agent_timeline", {"agent_id": str(agent.id), "limit": 5})
        self.assertEqual(initial_response.status_code, 200)
        initial_cursor = self._structured_content(initial_response)["latest_cursor"]

        agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=build_web_agent_address(agent.id),
            defaults={"owner_agent": agent},
        )
        if agent_endpoint.owner_agent_id != agent.id:
            agent_endpoint.owner_agent = agent
            agent_endpoint.save(update_fields=["owner_agent"])
        user_address = build_web_user_address(self.user.id, agent.id)
        user_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=user_address,
        )
        conversation, _ = PersistentAgentConversation.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=user_address,
            defaults={"owner_agent": agent, "display_name": self.user.email},
        )
        if conversation.owner_agent_id != agent.id:
            conversation.owner_agent = agent
            conversation.save(update_fields=["owner_agent"])
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=agent_endpoint,
            to_endpoint=user_endpoint,
            conversation=conversation,
            is_outbound=True,
            body="Agent reply visible through timeline.",
            raw_payload={"source": "unit_test"},
        )

        timeline_response = self._call_tool(
            "gobii_get_agent_timeline",
            {"agent_id": str(agent.id), "after_cursor": initial_cursor, "limit": 5},
        )
        self.assertEqual(timeline_response.status_code, 200)
        timeline_content = self._structured_content(timeline_response)
        self.assertEqual(timeline_content["events"][-1]["message"]["id"], str(message.id))

        wait_response = self._call_tool(
            "gobii_wait_for_agent_event",
            {
                "agent_id": str(agent.id),
                "after_cursor": initial_cursor,
                "timeout_seconds": 0,
                "event_types": ["message"],
                "filters": {
                    "from_actor_type": "agent",
                    "from_agent_id": str(agent.id),
                    "message_id": str(message.id),
                    "channel": "web",
                },
            },
        )
        self.assertEqual(wait_response.status_code, 200)
        wait_content = self._structured_content(wait_response)
        self.assertTrue(wait_content["matched"])
        self.assertFalse(wait_content["timed_out"])
        self.assertEqual(wait_content["events"][0]["message"]["id"], str(message.id))

        outbound_to_agent_response = self._call_tool(
            "gobii_wait_for_agent_event",
            {
                "agent_id": str(agent.id),
                "after_cursor": initial_cursor,
                "timeout_seconds": 0,
                "event_types": ["message"],
                "filters": {
                    "to_agent_id": str(agent.id),
                    "message_id": str(message.id),
                },
            },
        )
        self.assertEqual(outbound_to_agent_response.status_code, 200)
        outbound_to_agent_content = self._structured_content(outbound_to_agent_response)
        self.assertFalse(outbound_to_agent_content["matched"])
        self.assertTrue(outbound_to_agent_content["timed_out"])

    def test_agent_debug_trace_returns_bounded_sanitized_audit_info(self):
        agent = self._create_agent(self.user, "Debug Trace MCP Agent")
        self._create_web_message(
            agent,
            "Investigate the job. Authorization: Bearer message-secret-token",
        )
        completion = PersistentAgentCompletion.objects.create(
            agent=agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            response_id="resp-debug",
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
            cached_tokens=3,
            llm_model="debug-model",
            llm_provider="debug-provider",
            llm_tool_names=["http_request"],
            thinking_content="reasoning with api_key=thinking-secret-token",
            input_cost_total=Decimal("0.011"),
            input_cost_uncached=Decimal("0.010"),
            input_cost_cached=Decimal("0.001"),
            output_cost=Decimal("0.007"),
            total_cost=Decimal("0.018"),
            credits_cost=Decimal("0.500"),
            billed=True,
        )
        step = PersistentAgentStep.objects.create(
            agent=agent,
            completion=completion,
            description="Tool call with password=step-secret-token",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="http_request",
            tool_params={
                "url": "https://example.test/data",
                "api_key": "params-secret-token",
                "headers": {"Authorization": "Bearer params-bearer-secret"},
            },
            result="Fetched ok with sk-live-resultsecret1234567890",
            status="complete",
            execution_duration_ms=42,
        )
        PersistentAgentPromptArchive.objects.create(
            agent=agent,
            step=step,
            rendered_at=timezone.now(),
            storage_key="prompt_archives/raw-secret-storage-key.json.zst",
            raw_bytes=1000,
            compressed_bytes=400,
            tokens_before=100,
            tokens_after=80,
            tokens_saved=20,
        )
        PersistentAgentError.objects.create(
            agent=agent,
            completion=completion,
            category=PersistentAgentError.Category.TOOL_PERSISTENCE,
            source="tests.remote_mcp",
            message="Tool failed with access_token=error-secret-token",
            exception_class="RuntimeError",
            traceback="Traceback with Bearer traceback-secret-token",
            context={"password": "context-secret-token", "attempt": 1},
        )
        run = EvalRun.objects.create(
            agent=agent,
            initiated_by=self.user,
            scenario_slug="debug_trace_scenario",
            status=EvalRun.Status.COMPLETED,
        )
        EvalRunTask.objects.create(
            run=run,
            sequence=1,
            name="verify_trace",
            status=EvalRunTask.Status.FAILED,
            assertion_type="manual",
            observed_summary="Observed api_key=observed-secret-token",
            debug_artifacts={
                "params": {"url": "https://example.test/data", "api_key": "artifact-secret-token"},
                "messages": ["summary with Bearer artifact-bearer-secret"],
            },
            first_step=step,
        )

        response = self._call_tool(
            "gobii_get_agent_debug_trace",
            {
                "agent_id": str(agent.id),
                "limit": 10,
                "include": ["audit_events", "completions", "eval_debug_artifacts", "diagnostics"],
                "detail": "standard",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"]["isError"])
        content = self._structured_content(response)
        self.assertEqual(content["agent"]["id"], str(agent.id))
        self.assertEqual(content["audit"]["source"], "console.agent_audit.events.fetch_audit_events")
        self.assertLessEqual(len(content["audit_events"]), 10)
        event_kinds = {event["kind"] for event in content["audit_events"]}
        self.assertTrue({"message", "tool_call", "completion", "error"}.issubset(event_kinds))
        self.assertEqual(content["completions"]["items"][0]["cost"]["total_cost"], "0.018000")
        self.assertEqual(content["completions"]["items"][0]["prompt_archive"]["tokens_saved"], 20)
        self.assertEqual(content["eval_debug_artifacts"]["items"][0]["scenario_slug"], "debug_trace_scenario")
        self.assertGreaterEqual(content["diagnostics"]["recent_error_count"], 1)

        serialized = json.dumps(content)
        for secret in (
            "message-secret-token",
            "thinking-secret-token",
            "params-secret-token",
            "params-bearer-secret",
            "sk-live-resultsecret1234567890",
            "step-secret-token",
            "error-secret-token",
            "traceback-secret-token",
            "context-secret-token",
            "artifact-secret-token",
            "artifact-bearer-secret",
            "observed-secret-token",
            "raw-secret-storage-key",
        ):
            self.assertNotIn(secret, serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_agent_debug_trace_enforces_access_scope_for_other_org_agents(self):
        org = Organization.objects.create(name="MCP Org", slug="mcp-org", created_by=self.user)
        org.billing.purchased_seats = 1
        org.billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        raw_org_key, _ = ApiKey.create_for_org(org, created_by=self.user, name="mcp-org")
        other_org = Organization.objects.create(name="Other MCP Org", slug="other-mcp-org", created_by=self.other_user)
        other_org.billing.purchased_seats = 1
        other_org.billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=other_org,
            user=self.other_user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        other_agent = self._create_agent(self.other_user, "Other Org Debug Agent", organization=other_org)

        response = self._call_tool(
            "gobii_get_agent_debug_trace",
            {"agent_id": str(other_agent.id)},
            api_key=raw_org_key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["result"]["isError"])
        self.assertEqual(self._structured_content(response)["status"], "error")
        self.assertIn("not found or inaccessible", self._structured_content(response)["message"])

    def test_agent_debug_trace_limit_and_recent_window_are_enforced(self):
        agent = self._create_agent(self.user, "Debug Trace Window Agent")
        old_message = self._create_web_message(agent, "Old debug event")
        old_timestamp = timezone.now() - timedelta(hours=2)
        PersistentAgentMessage.objects.filter(id=old_message.id).update(timestamp=old_timestamp)

        for index in range(3):
            self._create_web_message(agent, f"Recent debug event {index}")

        response = self._call_tool(
            "gobii_get_agent_debug_trace",
            {
                "agent_id": str(agent.id),
                "limit": 2,
                "recent_minutes": 30,
                "include": ["audit_events"],
            },
        )

        self.assertEqual(response.status_code, 200)
        content = self._structured_content(response)
        self.assertLessEqual(len(content["audit_events"]), 2)
        bodies = [event.get("body_text") for event in content["audit_events"] if event.get("kind") == "message"]
        self.assertTrue(all(body != "Old debug event" for body in bodies))

        invalid_response = self._call_tool(
            "gobii_get_agent_debug_trace",
            {
                "agent_id": str(agent.id),
                "limit": 2,
                "recent_minutes": 30,
                "since": timezone.now().isoformat(),
            },
        )
        self.assertEqual(invalid_response.status_code, 200)
        self.assertTrue(invalid_response.json()["result"]["isError"])

    def test_origin_validation_rejects_untrusted_browser_origins(self):
        response = self._post_mcp(
            "initialize",
            extra_headers={"HTTP_ORIGIN": "https://untrusted.example"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], -32600)

    def test_inaccessible_agent_returns_tool_error(self):
        other_agent = self._create_agent(self.other_user, "Other User Agent")

        response = self._call_tool("gobii_get_agent", {"agent_id": str(other_agent.id)})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["result"]["isError"])
        self.assertEqual(self._structured_content(response)["status"], "error")
