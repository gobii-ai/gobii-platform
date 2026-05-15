import base64
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    IntelligenceTier,
    PersistentAgent,
    PersistentAgentMessage,
    UserQuota,
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
