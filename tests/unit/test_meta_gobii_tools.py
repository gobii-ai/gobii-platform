import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core.event_processing import _execute_tool_call_runtime
from api.agent.core.prompt_context import get_agent_tools
from api.agent.system_skills import get_system_skill_definition, shortlist_system_skills
from api.agent.system_skills.service import enable_system_skills
from api.agent.tools.meta_gobii import execute_meta_gobii_tool
from api.agent.tools.meta_gobii_names import (
    META_GOBII_LEGACY_SYSTEM_SKILL_KEY,
    META_GOBII_SYSTEM_SKILL_KEY,
    META_GOBII_TOOL_NAMES,
)
from api.agent.tools.tool_runtime import execute_runtime_tool_call
from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEmailEndpoint,
    PersistentAgentMessage,
    PersistentAgentSystemSkillState,
)


def _tool_names(tools: list[dict]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.add(function["name"])
    return names


def _mock_mcp_manager() -> MagicMock:
    manager = MagicMock()
    manager._initialized = True
    manager.get_tools_for_agent.return_value = []
    manager.get_enabled_tools_definitions.return_value = []
    manager.is_tool_blacklisted.return_value = False
    return manager


def _assert_confirmation_required(test_case: TestCase, result: dict):
    test_case.assertEqual(result["status"], "confirmation_required")
    test_case.assertIn("confirmation_prompt", result)
    test_case.assertTrue(result["requires_user_confirmed"])
    test_case.assertTrue(result["proposed_actions"])


@tag("batch_agent_tools")
class MetaGobiiSystemSkillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="meta-gobii-owner",
            email="meta-owner@example.com",
            password="secret",
        )
        cls.browser = BrowserUseAgent.objects.create(user=cls.user, name="Meta Manager Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Meta Manager",
            charter="Manage teams of Gobiis.",
            browser_use_agent=cls.browser,
        )

    def test_system_skill_is_discoverable_for_team_graph_queries_only(self):
        definition = get_system_skill_definition(META_GOBII_SYSTEM_SKILL_KEY)
        legacy_definition = get_system_skill_definition(META_GOBII_LEGACY_SYSTEM_SKILL_KEY)

        self.assertIsNotNone(definition)
        self.assertEqual(definition.name, "Meta Gobii")
        self.assertEqual(definition.skill_key, "meta_gobii")
        self.assertEqual(legacy_definition, definition)
        self.assertEqual(definition.tool_names, META_GOBII_TOOL_NAMES)
        self.assertIn("same owner or organization scope", definition.prompt_instructions)
        self.assertIn("Human approval boundary", definition.prompt_instructions)
        self.assertIn("user_confirmed=true", definition.prompt_instructions)
        self.assertIn("non-duplicated proposal", definition.prompt_instructions)
        self.assertIn("execute only that approved scope", definition.prompt_instructions)
        self.assertIn("avoid echoing full email addresses or phone numbers", definition.prompt_instructions)
        self.assertIn("meta_gobii_send_agent_message is a control-plane message injector, not a peer DM", definition.prompt_instructions)
        self.assertIn("use the newly available send_agent_message tool", definition.prompt_instructions)

        for query in [
            "help me create a team of Gobiis, link them, and brief them",
            "deploy Gobiis that supervise my sales and support graph",
            "configure a manager Gobii to restructure my Gobii control plane",
        ]:
            matches = shortlist_system_skills(query, available_tool_names=set(META_GOBII_TOOL_NAMES))
            self.assertEqual([match.skill_key for match in matches], [META_GOBII_SYSTEM_SKILL_KEY])

        for query in [
            "write a customer support reply that mentions Gobii",
            "summarize Gobii pricing for a normal content task",
        ]:
            negative_matches = shortlist_system_skills(query, available_tool_names=set(META_GOBII_TOOL_NAMES))
            self.assertEqual(negative_matches, [])

    @patch("api.agent.tools.tool_manager.get_mcp_manager")
    def test_meta_tools_are_hidden_until_system_skill_is_enabled(self, mock_get_manager):
        mock_get_manager.return_value = _mock_mcp_manager()

        before_names = _tool_names(get_agent_tools(self.agent))
        self.assertTrue(set(META_GOBII_TOOL_NAMES).isdisjoint(before_names))

        result = enable_system_skills(self.agent, [META_GOBII_SYSTEM_SKILL_KEY])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["enabled"], [META_GOBII_SYSTEM_SKILL_KEY])
        after_names = _tool_names(get_agent_tools(self.agent))
        self.assertTrue(set(META_GOBII_TOOL_NAMES).issubset(after_names))
        self.assertTrue(
            {
                "meta_gobii_create_agent",
                "meta_gobii_request_agent_creation",
                "meta_gobii_link_agents",
                "meta_gobii_send_agent_message",
                "meta_gobii_wait_for_agent_event",
            }.issubset(after_names)
        )
        self.assertNotIn("spawn_agent", after_names)

    def test_direct_tool_execution_requires_skill_state(self):
        result = execute_meta_gobii_tool(self.agent, "meta_gobii_list_agents", {})

        self.assertEqual(result["status"], "error")
        self.assertIn(META_GOBII_SYSTEM_SKILL_KEY, result["message"])

    def test_legacy_skill_state_alias_still_allows_direct_tools(self):
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=META_GOBII_LEGACY_SYSTEM_SKILL_KEY,
            is_enabled=True,
        )

        result = execute_meta_gobii_tool(self.agent, "meta_gobii_list_agents", {})

        self.assertEqual(result["status"], "ok")

    @patch("api.agent.tools.tool_manager.get_mcp_manager")
    def test_legacy_skill_key_enables_primary_meta_gobii_skill(self, mock_get_manager):
        mock_get_manager.return_value = _mock_mcp_manager()

        result = enable_system_skills(self.agent, [META_GOBII_LEGACY_SYSTEM_SKILL_KEY])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["enabled"], [META_GOBII_SYSTEM_SKILL_KEY])
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=META_GOBII_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )


@tag("batch_agent_tools")
class MetaGobiiDirectToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="meta-tool-owner",
            email="meta-tool-owner@example.com",
            password="secret",
        )
        cls.other_user = User.objects.create_user(
            username="meta-tool-other",
            email="meta-tool-other@example.com",
            password="secret",
        )

        cls.manager_browser = BrowserUseAgent.objects.create(user=cls.user, name="Tool Manager Browser")
        cls.manager = PersistentAgent.objects.create(
            user=cls.user,
            name="Tool Manager",
            charter="Manage Gobii teams.",
            browser_use_agent=cls.manager_browser,
        )
        PersistentAgentSystemSkillState.objects.create(
            agent=cls.manager,
            skill_key=META_GOBII_SYSTEM_SKILL_KEY,
            is_enabled=True,
        )

        cls.peer_browser = BrowserUseAgent.objects.create(user=cls.user, name="Peer Browser")
        cls.peer = PersistentAgent.objects.create(
            user=cls.user,
            name="Peer Gobii",
            charter="Handle recruiting.",
            browser_use_agent=cls.peer_browser,
        )

        cls.other_browser = BrowserUseAgent.objects.create(user=cls.other_user, name="Other Browser")
        cls.other_agent = PersistentAgent.objects.create(
            user=cls.other_user,
            name="Other Owner Gobii",
            charter="Should not be manageable.",
            browser_use_agent=cls.other_browser,
        )

    def test_list_and_get_are_scoped_to_same_personal_owner(self):
        listed = execute_meta_gobii_tool(self.manager, "meta_gobii_list_agents", {"page_size": 50})
        listed_ids = {agent["id"] for agent in listed["agents"]}

        self.assertEqual(listed["status"], "ok")
        self.assertIn(str(self.manager.id), listed_ids)
        self.assertIn(str(self.peer.id), listed_ids)
        self.assertNotIn(str(self.other_agent.id), listed_ids)

        denied = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_get_agent",
            {"agent_id": str(self.other_agent.id)},
        )
        self.assertEqual(denied["status"], "error")
        self.assertIn("not found or inaccessible", denied["message"].lower())

    @override_settings(ENABLE_DEFAULT_AGENT_EMAIL=True, DEFAULT_AGENT_EMAIL_DOMAIN="agents.test")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tools.meta_gobii.AgentService.has_agents_available", return_value=True)
    @patch("api.services.persistent_agents.AgentService.has_agents_available", return_value=True)
    @patch("api.models.AgentService.get_agents_available", return_value=10)
    def test_create_agent_uses_same_owner_and_confirmation_gate(
        self,
        _model_capacity,
        _provision_capacity,
        _tool_capacity,
        mock_delay,
    ):
        blocked = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_create_agent",
            {
                "name": "Sales Gobii",
                "charter": "Own sales follow-up.",
                "daily_credit_limit": 12,
            },
        )
        _assert_confirmation_required(self, blocked)
        self.assertFalse(PersistentAgent.objects.filter(user=self.user, name="Sales Gobii").exists())

        with self.captureOnCommitCallbacks(execute=True):
            created = execute_meta_gobii_tool(
                self.manager,
                "meta_gobii_create_agent",
                {
                    "name": "Sales Gobii",
                    "charter": "Own sales follow-up.",
                    "daily_credit_limit": 12,
                    "user_confirmed": True,
                },
            )

        self.assertEqual(created["status"], "ok")
        child = PersistentAgent.objects.get(id=created["agent"]["id"])
        self.assertEqual(child.user_id, self.user.id)
        self.assertIsNone(child.organization_id)
        self.assertEqual(child.daily_credit_limit, 12)
        self.assertEqual(child.planning_state, PersistentAgent.PlanningState.SKIPPED)
        agent_email_endpoint = child.comms_endpoints.get(channel=CommsChannel.EMAIL)
        self.assertTrue(agent_email_endpoint.is_primary)
        self.assertEqual(agent_email_endpoint.address, "sales.gobii@agents.test")
        email_meta = PersistentAgentEmailEndpoint.objects.get(endpoint=agent_email_endpoint)
        self.assertTrue(email_meta.verified)
        self.assertEqual(email_meta.display_name, "Sales Gobii")
        mock_delay.assert_called_with(str(child.id))

    @patch("api.services.agent_settings_resume.process_agent_events_task.delay")
    def test_update_agent_requires_confirmation_and_respects_access(self, _mock_delay):
        blocked = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_update_agent",
            {
                "agent_id": str(self.peer.id),
                "name": "Updated Peer Gobii",
            },
        )
        _assert_confirmation_required(self, blocked)
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.name, "Peer Gobii")

        updated = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_update_agent",
            {
                "agent_id": str(self.peer.id),
                "daily_credit_limit": 9,
                "user_confirmed": True,
            },
        )

        self.assertEqual(updated["status"], "ok")
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.daily_credit_limit, 9)
        self.assertEqual(updated["changed_fields"], ["daily_credit_limit"])

        denied = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_update_agent",
            {
                "agent_id": str(self.other_agent.id),
                "name": "Nope",
            },
        )
        self.assertEqual(denied["status"], "error")

    def test_link_and_unlink_accessible_agents_only_after_confirmation(self):
        blocked_link = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_link_agents",
            {
                "agent_id": str(self.manager.id),
                "peer_agent_id": str(self.peer.id),
                "messages_per_window": 10,
                "window_hours": 4,
            },
        )
        _assert_confirmation_required(self, blocked_link)
        self.assertFalse(
            AgentPeerLink.objects.filter(
                pair_key=AgentPeerLink.build_pair_key(self.manager.id, self.peer.id)
            ).exists()
        )

        result = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_link_agents",
            {
                "agent_id": str(self.manager.id),
                "peer_agent_id": str(self.peer.id),
                "messages_per_window": 10,
                "window_hours": 4,
                "user_confirmed": True,
            },
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["created"])
        link = AgentPeerLink.objects.get(id=result["link"]["id"])
        self.assertEqual(link.messages_per_window, 10)
        self.assertEqual(link.window_hours, 4)

        blocked_unlink = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_unlink_agents",
            {"peer_link_id": str(link.id)},
        )
        _assert_confirmation_required(self, blocked_unlink)

        unlinked = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_unlink_agents",
            {"peer_link_id": str(link.id), "user_confirmed": True},
        )
        self.assertEqual(unlinked["status"], "unlinked")
        self.assertFalse(AgentPeerLink.objects.filter(id=link.id).exists())

        denied = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_link_agents",
            {
                "agent_id": str(self.manager.id),
                "peer_agent_id": str(self.other_agent.id),
            },
        )
        self.assertEqual(denied["status"], "error")

    @patch("api.agent.tools.tool_manager.get_mcp_manager")
    def test_linking_invoking_agent_refreshes_peer_dm_tool_in_current_run(self, mock_get_manager):
        mock_get_manager.return_value = _mock_mcp_manager()
        self.assertNotIn("send_agent_message", _tool_names(get_agent_tools(self.manager)))

        linked, updated_tools = _execute_tool_call_runtime(
            self.manager,
            tool_name="meta_gobii_link_agents",
            exec_params={
                "agent_id": str(self.manager.id),
                "peer_agent_id": str(self.peer.id),
                "user_confirmed": True,
            },
            budget_ctx=None,
            eval_run_id=None,
        )

        self.assertEqual(linked["status"], "ok")
        self.assertEqual(linked["peer_messaging"]["tool_name"], "send_agent_message")
        self.assertEqual(linked["peer_messaging"]["peer_agent_id"], str(self.peer.id))
        self.assertIsNotNone(updated_tools)
        self.assertIn("send_agent_message", _tool_names(updated_tools))

        unlinked, updated_tools = execute_runtime_tool_call(
            self.manager,
            tool_name="meta_gobii_unlink_agents",
            exec_params={
                "peer_link_id": linked["link"]["id"],
                "user_confirmed": True,
            },
        )

        self.assertEqual(unlinked["status"], "unlinked")
        self.assertIsNotNone(updated_tools)
        self.assertNotIn("send_agent_message", _tool_names(updated_tools))

    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_send_agent_message_requires_confirmation_then_injects_internal_web_message(self, mock_delay):
        blocked = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_send_agent_message",
            {
                "agent_id": str(self.peer.id),
                "body": "Briefing: focus on recruiting signal and report blockers.",
                "trigger_processing": True,
            },
        )
        _assert_confirmation_required(self, blocked)
        self.assertFalse(
            PersistentAgentMessage.objects.filter(
                owner_agent=self.peer,
                body__contains="recruiting signal",
            ).exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = execute_meta_gobii_tool(
                self.manager,
                "meta_gobii_send_agent_message",
                {
                    "agent_id": str(self.peer.id),
                    "body": "Briefing: focus on recruiting signal and report blockers.",
                    "trigger_processing": True,
                    "user_confirmed": True,
                },
            )

        self.assertEqual(result["status"], "queued")
        message = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertEqual(message.owner_agent_id, self.peer.id)
        self.assertIn("recruiting signal", message.body)
        mock_delay.assert_called_once_with(str(self.peer.id))

    def test_contacts_pending_requests_and_endpoints(self):
        blocked_add = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_add_contact",
            {
                "agent_id": str(self.peer.id),
                "channel": "email",
                "address": "TEAM-MEMBER@EXAMPLE.COM",
                "allow_inbound": True,
                "allow_outbound": False,
            },
        )
        _assert_confirmation_required(self, blocked_add)
        self.assertFalse(CommsAllowlistEntry.objects.filter(agent=self.peer, channel=CommsChannel.EMAIL).exists())

        added = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_add_contact",
            {
                "agent_id": str(self.peer.id),
                "channel": "email",
                "address": "TEAM-MEMBER@EXAMPLE.COM",
                "allow_inbound": True,
                "allow_outbound": False,
                "user_confirmed": True,
            },
        )
        self.assertEqual(added["status"], "ok")
        self.assertTrue(added["created"])
        self.assertEqual(added["contact"]["address"], "team-member@example.com")
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.whitelist_policy, PersistentAgent.WhitelistPolicy.MANUAL)

        listed = execute_meta_gobii_tool(self.manager, "meta_gobii_list_contacts", {"agent_id": str(self.peer.id)})
        self.assertEqual(len(listed["contacts"]), 1)

        blocked_remove = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_remove_contact",
            {"agent_id": str(self.peer.id), "contact_id": added["contact"]["id"]},
        )
        _assert_confirmation_required(self, blocked_remove)

        removed = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_remove_contact",
            {
                "agent_id": str(self.peer.id),
                "contact_id": added["contact"]["id"],
                "user_confirmed": True,
            },
        )
        self.assertEqual(removed["status"], "ok")
        self.assertFalse(CommsAllowlistEntry.objects.get(id=added["contact"]["id"]).is_active)

        request = CommsAllowlistRequest.objects.create(
            agent=self.peer,
            channel=CommsChannel.EMAIL,
            address="ops@example.com",
            name="Ops Lead",
            reason="Coordinate launches.",
            purpose="Launch coordination",
            expires_at=timezone.now() + timedelta(days=1),
        )
        pending = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_list_pending_contacts",
            {"agent_id": str(self.peer.id)},
        )
        self.assertEqual([item["id"] for item in pending["requests"]], [str(request.id)])

        blocked_approve = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_approve_pending_contact",
            {
                "agent_id": str(self.peer.id),
                "request_id": str(request.id),
                "allow_inbound": True,
                "allow_outbound": True,
            },
        )
        _assert_confirmation_required(self, blocked_approve)
        request.refresh_from_db()
        self.assertEqual(request.status, CommsAllowlistRequest.RequestStatus.PENDING)

        approved = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_approve_pending_contact",
            {
                "agent_id": str(self.peer.id),
                "request_id": str(request.id),
                "allow_inbound": True,
                "allow_outbound": True,
                "user_confirmed": True,
            },
        )
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(
            CommsAllowlistEntry.objects.filter(
                agent=self.peer,
                channel=CommsChannel.EMAIL,
                address="ops@example.com",
                is_active=True,
            ).exists()
        )

        owner_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
            owner_agent=None,
        )
        blocked_endpoint = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_set_preferred_contact_endpoint",
            {"agent_id": str(self.peer.id), "endpoint_id": str(owner_endpoint.id)},
        )
        _assert_confirmation_required(self, blocked_endpoint)
        self.peer.refresh_from_db()
        self.assertIsNone(self.peer.preferred_contact_endpoint_id)

        endpoint_result = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_set_preferred_contact_endpoint",
            {
                "agent_id": str(self.peer.id),
                "endpoint_id": str(owner_endpoint.id),
                "user_confirmed": True,
            },
        )
        self.assertEqual(endpoint_result["status"], "ok")
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.preferred_contact_endpoint_id, owner_endpoint.id)

        endpoints = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_list_contact_endpoints",
            {"agent_id": str(self.peer.id)},
        )
        self.assertEqual([endpoint["id"] for endpoint in endpoints["endpoints"]], [str(owner_endpoint.id)])

    def test_tool_definitions_are_json_serializable(self):
        definition = get_system_skill_definition(META_GOBII_SYSTEM_SKILL_KEY)

        json.dumps(definition.tool_names)
