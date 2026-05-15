from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core.prompt_context import _get_system_instruction, get_agent_tools
from api.agent.tools.meta_gobii import execute_meta_gobii_tool
from api.agent.tools.meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY
from api.agent.tools.spawn_agent import execute_spawn_agent
from api.agent.tools.tool_runtime import execute_runtime_tool_call
from api.models import (
    AgentSpawnRequest,
    BrowserUseAgent,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentSystemSkillState,
)


@tag("batch_agent_tools")
class SpawnAgentToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="spawn-owner",
            email="spawn-owner@example.com",
            password="secret",
        )

        cls.personal_browser = BrowserUseAgent.objects.create(user=cls.user, name="Spawn Personal Browser")
        cls.personal_agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Parent Personal Agent",
            charter="Handle personal planning work",
            browser_use_agent=cls.personal_browser,
        )

        cls.organization = Organization.objects.create(
            name="Spawn Org",
            slug="spawn-org",
            created_by=cls.user,
        )
        org_billing = cls.organization.billing
        org_billing.purchased_seats = 1
        org_billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=cls.organization,
            user=cls.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        cls.org_browser = BrowserUseAgent.objects.create(user=cls.user, name="Spawn Org Browser")
        cls.org_agent = PersistentAgent.objects.create(
            user=cls.user,
            organization=cls.organization,
            name="Parent Org Agent",
            charter="Handle org research work",
            browser_use_agent=cls.org_browser,
        )

    def _enable_meta_gobii(self, agent):
        PersistentAgentSystemSkillState.objects.update_or_create(
            agent=agent,
            skill_key=META_GOBII_SYSTEM_SKILL_KEY,
            defaults={"is_enabled": True},
        )

    def test_get_agent_tools_does_not_include_spawn_agent_when_capacity_exists(self):
        with patch("agents.services.AgentService.get_agents_available", return_value=2):
            tools = get_agent_tools(self.personal_agent)
        tool_names = [entry.get("function", {}).get("name") for entry in tools if isinstance(entry, dict)]
        self.assertNotIn("spawn_agent", tool_names)

    def test_base_prompt_does_not_advertise_spawn_agent_when_meta_gobii_is_disabled(self):
        prompt = _get_system_instruction(self.personal_agent, is_first_run=False)

        self.assertNotIn("spawn_agent", prompt)
        self.assertNotIn("specialist peer", prompt)
        self.assertNotIn("team of Gobiis", prompt)

    def test_stale_spawn_agent_runtime_call_requires_meta_gobii_without_creating_request(self):
        params = {
            "charter": "Own contract review and summarize legal risk.",
            "handoff_message": "Review attached SOW and return redlines.",
            "reason": "Contract law review is outside my normal scope.",
            "will_continue_work": False,
        }

        result, updated_tools = execute_runtime_tool_call(
            self.personal_agent,
            tool_name="spawn_agent",
            exec_params=params,
        )

        self.assertIsNone(updated_tools)
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Meta Gobii", result.get("message", ""))
        self.assertFalse(AgentSpawnRequest.objects.filter(agent=self.personal_agent).exists())

    def test_meta_gobii_request_agent_creation_creates_pending_request_with_org_context_urls(self):
        self._enable_meta_gobii(self.org_agent)
        params = {
            "charter": "Own contract review and summarize legal risk.",
            "handoff_message": "Review attached SOW and return redlines.",
            "reason": "Contract law review is outside my normal scope.",
            "will_continue_work": False,
        }

        with patch("api.agent.tools.spawn_agent.AgentService.has_agents_available", return_value=True):
            result = execute_meta_gobii_tool(self.org_agent, "meta_gobii_request_agent_creation", params)

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("request_status"), AgentSpawnRequest.RequestStatus.PENDING)
        self.assertEqual(result.get("created_count"), 1)
        self.assertTrue(result.get("approval_url"))
        self.assertTrue(result.get("decision_api_url"))
        self.assertTrue(result.get("auto_sleep_ok"))

        approval_url = result["approval_url"]
        approval_query = parse_qs(urlparse(approval_url).query)
        self.assertEqual(approval_query.get("context_type"), ["organization"])
        self.assertEqual(approval_query.get("context_id"), [str(self.organization.id)])

        decision_url = result["decision_api_url"]
        decision_query = parse_qs(urlparse(decision_url).query)
        self.assertEqual(decision_query.get("context_type"), ["organization"])
        self.assertEqual(decision_query.get("context_id"), [str(self.organization.id)])

        spawn_request = AgentSpawnRequest.objects.get(id=result["spawn_request_id"])
        self.assertEqual(spawn_request.agent_id, self.org_agent.id)
        self.assertEqual(
            spawn_request.requested_charter,
            "Own contract review and summarize legal risk.",
        )

    def test_meta_gobii_request_agent_creation_reuses_matching_pending_request(self):
        self._enable_meta_gobii(self.personal_agent)
        params = {
            "charter": "Own outbound vendor coordination and contract follow-ups.",
            "handoff_message": "Pick up vendor renewals this week and report blockers.",
            "reason": "Vendor operations are outside my charter.",
            "will_continue_work": True,
        }

        with patch("api.agent.tools.spawn_agent.AgentService.has_agents_available", return_value=True):
            first = execute_meta_gobii_tool(self.personal_agent, "meta_gobii_request_agent_creation", params)
            second = execute_meta_gobii_tool(self.personal_agent, "meta_gobii_request_agent_creation", params)

        self.assertEqual(first.get("status"), "ok")
        self.assertEqual(first.get("created_count"), 1)
        self.assertEqual(first.get("already_pending_count"), 0)
        self.assertEqual(second.get("status"), "ok")
        self.assertEqual(second.get("created_count"), 0)
        self.assertEqual(second.get("already_pending_count"), 1)
        self.assertEqual(second.get("spawn_request_id"), first.get("spawn_request_id"))
        self.assertEqual(
            AgentSpawnRequest.objects.filter(agent=self.personal_agent).count(),
            1,
        )

    def test_meta_gobii_request_agent_creation_after_decline_creates_new_request(self):
        self._enable_meta_gobii(self.personal_agent)
        params = {
            "charter": "Own outbound vendor coordination and contract follow-ups.",
            "handoff_message": "Pick up vendor renewals this week and report blockers.",
            "reason": "Vendor operations are outside my charter.",
            "will_continue_work": True,
        }

        with patch("api.agent.tools.spawn_agent.AgentService.has_agents_available", return_value=True):
            first = execute_meta_gobii_tool(self.personal_agent, "meta_gobii_request_agent_creation", params)
            first_request = AgentSpawnRequest.objects.get(id=first["spawn_request_id"])
            first_request.reject(self.user)
            second = execute_meta_gobii_tool(self.personal_agent, "meta_gobii_request_agent_creation", params)

        self.assertEqual(first.get("created_count"), 1)
        self.assertEqual(second.get("created_count"), 1)
        self.assertNotEqual(second.get("spawn_request_id"), first.get("spawn_request_id"))
        self.assertEqual(
            AgentSpawnRequest.objects.filter(agent=self.personal_agent).count(),
            2,
        )

    def test_meta_gobii_request_agent_creation_after_pending_request_expires_creates_new_request(self):
        self._enable_meta_gobii(self.personal_agent)
        params = {
            "charter": "Own outbound vendor coordination and contract follow-ups.",
            "handoff_message": "Pick up vendor renewals this week and report blockers.",
            "reason": "Vendor operations are outside my charter.",
            "will_continue_work": True,
        }
        expired_request = AgentSpawnRequest.objects.create(
            agent=self.personal_agent,
            requested_charter=params["charter"],
            handoff_message=params["handoff_message"],
            request_reason=params["reason"],
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        with patch("api.agent.tools.spawn_agent.AgentService.has_agents_available", return_value=True):
            result = execute_meta_gobii_tool(self.personal_agent, "meta_gobii_request_agent_creation", params)

        expired_request.refresh_from_db()
        self.assertEqual(expired_request.status, AgentSpawnRequest.RequestStatus.EXPIRED)
        self.assertIsNotNone(expired_request.responded_at)
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("created_count"), 1)
        self.assertEqual(result.get("already_pending_count"), 0)
        self.assertNotEqual(result.get("spawn_request_id"), str(expired_request.id))
        self.assertEqual(
            AgentSpawnRequest.objects.filter(agent=self.personal_agent).count(),
            2,
        )

    def test_legacy_spawn_agent_alias_still_works_after_meta_gobii_is_enabled(self):
        self._enable_meta_gobii(self.personal_agent)
        params = {
            "charter": "Own outbound vendor coordination and contract follow-ups.",
            "handoff_message": "Pick up vendor renewals this week and report blockers.",
            "reason": "Vendor operations are outside my charter.",
            "will_continue_work": True,
        }

        with patch("api.agent.tools.spawn_agent.AgentService.has_agents_available", return_value=True):
            result = execute_spawn_agent(self.personal_agent, params)

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("request_status"), AgentSpawnRequest.RequestStatus.PENDING)

    @override_settings(ENABLE_DEFAULT_AGENT_EMAIL=True, DEFAULT_AGENT_EMAIL_DOMAIN="agents.test")
    def test_spawn_request_approve_creates_peer_link_and_handoff(self):
        spawn_request = AgentSpawnRequest.objects.create(
            agent=self.personal_agent,
            requested_charter="Own outbound vendor coordination and contract follow-ups.",
            handoff_message="Pick up vendor renewals this week and report blockers.",
        )

        child_browser = BrowserUseAgent.objects.create(user=self.user, name="Spawned Child Browser")
        child_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Spawned Child Agent",
            charter="Own outbound vendor coordination and contract follow-ups.",
            browser_use_agent=child_browser,
        )

        with patch("api.models.AgentService.has_agents_available", return_value=True), patch(
            "api.services.persistent_agents.PersistentAgentProvisioningService.provision",
            return_value=SimpleNamespace(agent=child_agent),
        ), patch("api.agent.peer_comm.PeerMessagingService.send_message") as send_message:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                spawned_agent, link = spawn_request.approve(self.user)
                send_message.assert_not_called()

            for callback in callbacks:
                callback()

        spawn_request.refresh_from_db()
        self.assertEqual(spawn_request.status, AgentSpawnRequest.RequestStatus.APPROVED)
        self.assertEqual(spawn_request.responded_by_id, self.user.id)
        self.assertEqual(spawn_request.spawned_agent_id, child_agent.id)
        self.assertEqual(spawn_request.peer_link_id, link.id)
        self.assertEqual(spawned_agent.id, child_agent.id)
        send_message.assert_called_once_with(
            "Pick up vendor renewals this week and report blockers."
        )
        child_agent.refresh_from_db()
        self.assertIsNotNone(child_agent.preferred_contact_endpoint)
        self.assertEqual(child_agent.preferred_contact_endpoint.channel, CommsChannel.EMAIL)
        self.assertEqual(child_agent.preferred_contact_endpoint.address, self.user.email)
        agent_email_endpoint = child_agent.comms_endpoints.filter(
            owner_agent=child_agent,
            channel=CommsChannel.EMAIL,
        ).first()
        if agent_email_endpoint is not None:
            self.assertTrue(agent_email_endpoint.is_primary)
            self.assertIn("@", agent_email_endpoint.address)
            self.assertNotEqual(agent_email_endpoint.address, self.user.email.lower())
