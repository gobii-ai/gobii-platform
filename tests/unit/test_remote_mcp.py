import base64
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    IntelligenceTier,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
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

    def _post_mcp(self, method, params=None, *, auth="x-api-key", extra_headers=None):
        payload = {
            "jsonrpc": "2.0",
            "id": "test-1",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        headers = dict(extra_headers or {})
        if auth == "x-api-key":
            headers["HTTP_X_API_KEY"] = self.raw_api_key
        elif auth == "bearer":
            headers["HTTP_AUTHORIZATION"] = f"Bearer {self.raw_api_key}"
        return self.client.post(
            "/api/v1/mcp/",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def _call_tool(self, name, arguments=None, *, auth="x-api-key"):
        return self._post_mcp(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            auth=auth,
        )

    def _create_agent(self, user, name):
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=user, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=user,
            name=name,
            charter=f"{name} charter",
            browser_use_agent=browser_agent,
            preferred_llm_tier=self.tier,
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
        self.assertIn("gobii_upload_agent_file", tool_names)
        self.assertIn("gobii_list_agent_contacts", tool_names)
        self.assertIn("gobii_add_agent_contact", tool_names)
        self.assertIn("gobii_remove_agent_contact", tool_names)
        self.assertIn("gobii_list_pending_agent_contacts", tool_names)
        self.assertIn("gobii_approve_pending_agent_contact", tool_names)
        self.assertIn("gobii_list_agent_contact_endpoints", tool_names)
        self.assertIn("gobii_set_agent_preferred_contact_endpoint", tool_names)

        list_response = self._call_tool("gobii_list_agents")
        self.assertEqual(list_response.status_code, 200)
        content = self._structured_content(list_response)
        self.assertEqual(content["total"], 1)
        self.assertEqual(content["agents"][0]["id"], str(agent.id))

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

    def test_contact_tools_manage_allowlist_and_preferred_endpoint(self):
        agent = self._create_agent(self.user, "Contact MCP Agent")

        empty_response = self._call_tool("gobii_list_agent_contacts", {"agent_id": str(agent.id)})
        self.assertEqual(empty_response.status_code, 200)
        empty_content = self._structured_content(empty_response)
        self.assertEqual(empty_content["contacts"], [])
        self.assertEqual(empty_content["total"], 0)

        add_response = self._call_tool(
            "gobii_add_agent_contact",
            {
                "agent_id": str(agent.id),
                "channel": "email",
                "address": "Friend@Example.COM",
                "allow_inbound": False,
                "allow_outbound": True,
                "can_configure": True,
            },
        )
        self.assertEqual(add_response.status_code, 200)
        add_content = self._structured_content(add_response)
        self.assertFalse(add_response.json()["result"]["isError"])
        self.assertTrue(add_content["created"])
        self.assertEqual(add_content["contact"]["address"], "friend@example.com")
        self.assertFalse(add_content["contact"]["allow_inbound"])
        self.assertTrue(add_content["contact"]["allow_outbound"])
        self.assertTrue(add_content["contact"]["can_configure"])

        contact_id = add_content["contact"]["contact_id"]
        duplicate_response = self._call_tool(
            "gobii_add_agent_contact",
            {
                "agent_id": str(agent.id),
                "channel": "email",
                "address": "friend@example.com",
                "allow_inbound": True,
                "allow_outbound": False,
                "can_configure": False,
            },
        )
        self.assertEqual(duplicate_response.status_code, 200)
        duplicate_content = self._structured_content(duplicate_response)
        self.assertFalse(duplicate_content["created"])
        self.assertTrue(duplicate_content["updated"])
        self.assertEqual(
            CommsAllowlistEntry.objects.filter(agent=agent, channel=CommsChannel.EMAIL, address="friend@example.com").count(),
            1,
        )

        entry = CommsAllowlistEntry.objects.get(id=contact_id)
        entry.is_active = False
        entry.save(update_fields=["is_active"])

        reactivate_response = self._call_tool(
            "gobii_add_agent_contact",
            {
                "agent_id": str(agent.id),
                "channel": "email",
                "address": "friend@example.com",
            },
        )
        self.assertEqual(reactivate_response.status_code, 200)
        reactivate_content = self._structured_content(reactivate_response)
        self.assertFalse(reactivate_content["created"])
        self.assertTrue(reactivate_content["reactivated"])
        self.assertEqual(reactivate_content["contact"]["status"], "allowed")

        external_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="friend@example.com",
        )
        set_external_contact_response = self._call_tool(
            "gobii_set_agent_preferred_contact_endpoint",
            {"agent_id": str(agent.id), "contact_id": contact_id},
        )
        self.assertEqual(set_external_contact_response.status_code, 200)
        self.assertTrue(set_external_contact_response.json()["result"]["isError"])
        agent.refresh_from_db()
        self.assertIsNone(agent.preferred_contact_endpoint_id)

        set_external_endpoint_response = self._call_tool(
            "gobii_set_agent_preferred_contact_endpoint",
            {"agent_id": str(agent.id), "endpoint_id": str(external_endpoint.id)},
        )
        self.assertEqual(set_external_endpoint_response.status_code, 200)
        self.assertTrue(set_external_endpoint_response.json()["result"]["isError"])
        agent.refresh_from_db()
        self.assertIsNone(agent.preferred_contact_endpoint_id)

        owner_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        set_preferred_response = self._call_tool(
            "gobii_set_agent_preferred_contact_endpoint",
            {"agent_id": str(agent.id), "endpoint_id": str(owner_endpoint.id)},
        )
        self.assertEqual(set_preferred_response.status_code, 200)
        preferred_content = self._structured_content(set_preferred_response)
        self.assertFalse(set_preferred_response.json()["result"]["isError"])
        self.assertTrue(preferred_content["changed"])
        endpoint_id = preferred_content["preferred_contact_endpoint"]["endpoint_id"]
        self.assertEqual(endpoint_id, str(owner_endpoint.id))
        agent.refresh_from_db()
        self.assertEqual(str(agent.preferred_contact_endpoint_id), endpoint_id)

        endpoints_response = self._call_tool(
            "gobii_list_agent_contact_endpoints",
            {"agent_id": str(agent.id)},
        )
        self.assertEqual(endpoints_response.status_code, 200)
        endpoints = self._structured_content(endpoints_response)["endpoints"]
        self.assertEqual(len(endpoints), 1)
        self.assertEqual(endpoints[0]["endpoint_id"], endpoint_id)
        self.assertTrue(endpoints[0]["is_preferred_contact"])
        self.assertTrue(endpoints[0]["can_be_preferred"])
        self.assertIn("owner_user_contact", endpoints[0]["roles"])
        self.assertNotEqual(endpoints[0]["endpoint_id"], str(external_endpoint.id))

        contacts_response = self._call_tool("gobii_list_agent_contacts", {"agent_id": str(agent.id)})
        self.assertEqual(contacts_response.status_code, 200)
        contacts = self._structured_content(contacts_response)["contacts"]
        self.assertEqual(len(contacts), 1)
        self.assertFalse(contacts[0]["is_preferred"])

        remove_response = self._call_tool(
            "gobii_remove_agent_contact",
            {
                "agent_id": str(agent.id),
                "channel": "email",
                "address": "friend@example.com",
            },
        )
        self.assertEqual(remove_response.status_code, 200)
        remove_content = self._structured_content(remove_response)
        self.assertTrue(remove_content["removed"])
        self.assertFalse(remove_content["cleared_preferred_contact_endpoint"])
        self.assertFalse(CommsAllowlistEntry.objects.filter(id=contact_id).exists())
        agent.refresh_from_db()
        self.assertEqual(agent.preferred_contact_endpoint_id, owner_endpoint.id)

        clear_response = self._call_tool(
            "gobii_set_agent_preferred_contact_endpoint",
            {"agent_id": str(agent.id), "clear": True},
        )
        self.assertEqual(clear_response.status_code, 200)
        clear_content = self._structured_content(clear_response)
        self.assertFalse(clear_response.json()["result"]["isError"])
        self.assertEqual(clear_content["status"], "cleared")
        self.assertTrue(clear_content["changed"])
        agent.refresh_from_db()
        self.assertIsNone(agent.preferred_contact_endpoint_id)

    def test_pending_contact_tools_approve_and_reactivate_existing_entry(self):
        agent = self._create_agent(self.user, "Pending Contact MCP Agent")
        inactive = CommsAllowlistEntry.objects.create(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address="pending@example.com",
            is_active=False,
            allow_inbound=False,
            allow_outbound=False,
        )
        pending = CommsAllowlistRequest.objects.create(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address="pending@example.com",
            name="Pending Person",
            reason="Need to coordinate",
            purpose="Schedule meeting",
            request_inbound=True,
            request_outbound=True,
            request_configure=True,
            expires_at=timezone.now() + timedelta(days=1),
        )

        list_response = self._call_tool(
            "gobii_list_pending_agent_contacts",
            {"agent_id": str(agent.id)},
        )
        self.assertEqual(list_response.status_code, 200)
        pending_contacts = self._structured_content(list_response)["pending_contacts"]
        self.assertEqual(len(pending_contacts), 1)
        self.assertEqual(pending_contacts[0]["pending_contact_id"], str(pending.id))
        self.assertTrue(pending_contacts[0]["can_approve"])

        approve_response = self._call_tool(
            "gobii_approve_pending_agent_contact",
            {
                "agent_id": str(agent.id),
                "channel": "email",
                "address": "pending@example.com",
            },
        )
        self.assertEqual(approve_response.status_code, 200)
        approve_content = self._structured_content(approve_response)
        self.assertFalse(approve_content["created"])
        self.assertTrue(approve_content["updated"])
        self.assertTrue(approve_content["reactivated"])
        self.assertEqual(approve_content["pending_contact"]["status"], CommsAllowlistRequest.RequestStatus.APPROVED)

        inactive.refresh_from_db()
        self.assertTrue(inactive.is_active)
        self.assertTrue(inactive.allow_inbound)
        self.assertTrue(inactive.allow_outbound)
        self.assertTrue(inactive.can_configure)
        pending.refresh_from_db()
        self.assertEqual(pending.status, CommsAllowlistRequest.RequestStatus.APPROVED)

        second_approve_response = self._call_tool(
            "gobii_approve_pending_agent_contact",
            {"agent_id": str(agent.id), "pending_contact_id": str(pending.id)},
        )
        self.assertEqual(second_approve_response.status_code, 200)
        self.assertTrue(second_approve_response.json()["result"]["isError"])

    def test_contact_tools_enforce_agent_and_endpoint_access(self):
        agent = self._create_agent(self.user, "Access Contact MCP Agent")
        other_agent = self._create_agent(self.other_user, "Other Contact MCP Agent")

        inaccessible_add = self._call_tool(
            "gobii_add_agent_contact",
            {
                "agent_id": str(other_agent.id),
                "channel": "email",
                "address": "blocked@example.com",
            },
        )
        self.assertEqual(inaccessible_add.status_code, 200)
        self.assertTrue(inaccessible_add.json()["result"]["isError"])
        self.assertFalse(
            CommsAllowlistEntry.objects.filter(agent=other_agent, address="blocked@example.com").exists()
        )

        agent_owned_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address="agent-sender@example.com",
        )
        agent_owned_response = self._call_tool(
            "gobii_set_agent_preferred_contact_endpoint",
            {"agent_id": str(agent.id), "endpoint_id": str(agent_owned_endpoint.id)},
        )
        self.assertEqual(agent_owned_response.status_code, 200)
        self.assertTrue(agent_owned_response.json()["result"]["isError"])
        agent.refresh_from_db()
        self.assertIsNone(agent.preferred_contact_endpoint_id)

        other_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=other_agent,
            channel=CommsChannel.EMAIL,
            address="other-agent@example.com",
        )
        set_response = self._call_tool(
            "gobii_set_agent_preferred_contact_endpoint",
            {"agent_id": str(agent.id), "endpoint_id": str(other_endpoint.id)},
        )
        self.assertEqual(set_response.status_code, 200)
        self.assertTrue(set_response.json()["result"]["isError"])
        agent.refresh_from_db()
        self.assertIsNone(agent.preferred_contact_endpoint_id)

        legacy_preferred = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="legacy-preferred@example.com",
        )
        agent.preferred_contact_endpoint = legacy_preferred
        agent.save(update_fields=["preferred_contact_endpoint"])

        keep_current_response = self._call_tool(
            "gobii_set_agent_preferred_contact_endpoint",
            {"agent_id": str(agent.id), "endpoint_id": str(legacy_preferred.id)},
        )
        self.assertEqual(keep_current_response.status_code, 200)
        self.assertFalse(keep_current_response.json()["result"]["isError"])
        self.assertFalse(self._structured_content(keep_current_response)["changed"])

        legacy_endpoints_response = self._call_tool(
            "gobii_list_agent_contact_endpoints",
            {"agent_id": str(agent.id)},
        )
        self.assertEqual(legacy_endpoints_response.status_code, 200)
        legacy_endpoints = {
            endpoint["endpoint_id"]: endpoint
            for endpoint in self._structured_content(legacy_endpoints_response)["endpoints"]
        }
        self.assertFalse(legacy_endpoints[str(agent_owned_endpoint.id)]["can_be_preferred"])
        self.assertTrue(legacy_endpoints[str(legacy_preferred.id)]["is_preferred_contact"])
        self.assertFalse(legacy_endpoints[str(legacy_preferred.id)]["can_be_preferred"])

    def test_contact_tools_return_structured_validation_errors(self):
        agent = self._create_agent(self.user, "Validation Contact MCP Agent")

        invalid_email_response = self._call_tool(
            "gobii_add_agent_contact",
            {
                "agent_id": str(agent.id),
                "channel": "email",
                "address": "not an email",
            },
        )
        self.assertEqual(invalid_email_response.status_code, 200)
        invalid_email_content = self._structured_content(invalid_email_response)
        self.assertTrue(invalid_email_response.json()["result"]["isError"])
        self.assertEqual(invalid_email_content["status"], "error")
        self.assertIn("valid email", invalid_email_content["message"])

        unsupported_channel_response = self._call_tool(
            "gobii_add_agent_contact",
            {
                "agent_id": str(agent.id),
                "channel": "slack",
                "address": "person@example.com",
            },
        )
        self.assertEqual(unsupported_channel_response.status_code, 200)
        self.assertTrue(unsupported_channel_response.json()["result"]["isError"])

        expired = CommsAllowlistRequest.objects.create(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address="expired@example.com",
            reason="Expired request",
            purpose="Test",
            expires_at=timezone.now() - timedelta(hours=1),
        )
        expired_response = self._call_tool(
            "gobii_approve_pending_agent_contact",
            {"agent_id": str(agent.id), "pending_contact_id": str(expired.id)},
        )
        self.assertEqual(expired_response.status_code, 200)
        expired_content = self._structured_content(expired_response)
        self.assertTrue(expired_response.json()["result"]["isError"])
        self.assertTrue(expired_content["details"]["is_expired"])

    def test_file_upload_and_message_attachment_path(self):
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
        message = PersistentAgentMessage.objects.get(id=message_id)
        attachment = message.attachments.get()
        self.assertEqual(str(attachment.filespace_node_id), node_id)
        process_delay.assert_called_once_with(str(agent.id))

    def test_timeline_cursor_reads_and_wait_filters(self):
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
                    "from_actor_type": "human_user",
                    "to_agent_id": str(agent.id),
                    "message_id": send_content["message_id"],
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
