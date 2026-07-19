import json
from unittest.mock import ANY, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag

from api.agent.tools.spawn_agent import execute_spawn_agent
from api.models import (
    AgentPeerLink,
    AgentSpawnRequest,
    BrowserUseAgent,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)


@tag("batch_agent_chat")
class SpawnAgentRequestDecisionAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="spawn-api-owner",
            email="spawn-api-owner@example.com",
            password="secret",
        )
        cls.member = User.objects.create_user(
            username="spawn-api-member",
            email="spawn-api-member@example.com",
            password="secret",
        )
        cls.admin = User.objects.create_user(
            username="spawn-api-admin",
            email="spawn-api-admin@example.com",
            password="secret",
        )

        cls.org = Organization.objects.create(
            name="Spawn API Org",
            slug="spawn-api-org",
            created_by=cls.owner,
        )
        org_billing = cls.org.billing
        org_billing.purchased_seats = 1
        org_billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=cls.org,
            user=cls.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            org=cls.org,
            user=cls.member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            org=cls.org,
            user=cls.admin,
            role=OrganizationMembership.OrgRole.ADMIN,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        browser_agent = BrowserUseAgent.objects.create(
            user=cls.owner,
            name="Spawn API Browser",
        )
        cls.agent = PersistentAgent.objects.create(
            user=cls.owner,
            organization=cls.org,
            name="Spawn API Parent",
            charter="Handle product operations.",
            browser_use_agent=browser_agent,
        )

    def setUp(self):
        self.client = Client()

    def _set_org_context(self, client: Client):
        session = client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session.save()

    def _create_spawn_request(self) -> AgentSpawnRequest:
        return AgentSpawnRequest.objects.create(
            agent=self.agent,
            requested_charter="Own competitive pricing monitoring and weekly deltas.",
            handoff_message="Take over pricing analysis and send me the first summary.",
        )

    def _create_meta_gobii_spawn_request(self) -> AgentSpawnRequest:
        result = execute_spawn_agent(
            self.agent,
            {
                "charter": "Own Meta Gobii team creation follow-up and role coordination.",
                "handoff_message": "Take over the approved Meta Gobii team setup and send the first status update.",
                "reason": "Meta Gobii team creation needs a specialist peer after human approval.",
                "will_continue_work": True,
            },
            invoked_via_meta_gobii=True,
        )
        self.assertEqual(result.get("status"), "ok")
        return AgentSpawnRequest.objects.get(id=result["spawn_request_id"])

    def test_org_member_cannot_resolve_spawn_request(self):
        spawn_request = self._create_spawn_request()
        self.client.force_login(self.member)
        self._set_org_context(self.client)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/",
                data=json.dumps({"decision": "decline"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 403)
        spawn_request.refresh_from_db()
        self.assertEqual(spawn_request.status, AgentSpawnRequest.RequestStatus.PENDING)

    def test_spawn_request_status_get_reflects_current_state(self):
        spawn_request = self._create_spawn_request()
        self.client.force_login(self.owner)
        self._set_org_context(self.client)

        pending_response = self.client.get(
            f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/"
        )
        self.assertEqual(pending_response.status_code, 200)
        self.assertEqual(
            pending_response.json().get("request_status"),
            AgentSpawnRequest.RequestStatus.PENDING,
        )
        self.assertIn("pending_action_requests", pending_response.json())

        spawn_request.reject(self.owner)
        rejected_response = self.client.get(
            f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/"
        )
        self.assertEqual(rejected_response.status_code, 200)
        self.assertEqual(
            rejected_response.json().get("request_status"),
            AgentSpawnRequest.RequestStatus.REJECTED,
        )

    def test_org_admin_can_decline_spawn_request(self):
        spawn_request = self._create_spawn_request()
        self.client.force_login(self.admin)
        self._set_org_context(self.client)

        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/",
            data=json.dumps({"decision": "decline"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("request_status"), AgentSpawnRequest.RequestStatus.REJECTED)
        self.assertIn("pending_action_requests", payload)
        spawn_request.refresh_from_db()
        self.assertEqual(spawn_request.status, AgentSpawnRequest.RequestStatus.REJECTED)
        self.assertEqual(spawn_request.responded_by_id, self.admin.id)

    def test_org_owner_can_decline_spawn_request(self):
        spawn_request = self._create_spawn_request()
        self.client.force_login(self.owner)
        self._set_org_context(self.client)

        response = self.client.post(
            f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/",
            data=json.dumps({"decision": "decline"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("request_status"), AgentSpawnRequest.RequestStatus.REJECTED)

        spawn_request.refresh_from_db()
        self.assertEqual(spawn_request.status, AgentSpawnRequest.RequestStatus.REJECTED)
        self.assertEqual(spawn_request.responded_by_id, self.owner.id)

        step = PersistentAgentStep.objects.filter(agent=self.agent).order_by("-created_at").first()
        self.assertIsNotNone(step)
        self.assertIn("declined", step.description.lower())
        system_step = getattr(step, "system_step", None)
        self.assertIsNotNone(system_step)
        self.assertEqual(system_step.code, PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @patch("console.agent_chat.access.can_user_use_personal_agents_and_api", return_value=False)
    @patch("console.agent_chat.access.can_user_access_personal_agent_chat", return_value=True)
    def test_delinquent_personal_owner_can_decline_spawn_request(
        self,
        _mock_can_access_personal_agent_chat,
        _mock_can_use_personal_agents_and_api,
    ):
        browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Spawn API Personal Browser",
        )
        personal_agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Spawn API Personal Parent",
            charter="Handle personal operations.",
            browser_use_agent=browser_agent,
        )
        spawn_request = AgentSpawnRequest.objects.create(
            agent=personal_agent,
            requested_charter="Handle scheduling followups.",
            handoff_message="Take over followup scheduling.",
        )
        self.client.force_login(self.owner)
        session = self.client.session
        session["context_type"] = "personal"
        session.save()

        response = self.client.post(
            f"/console/api/agents/{personal_agent.id}/spawn-requests/{spawn_request.id}/decision/",
            data=json.dumps({"decision": "decline"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("request_status"), AgentSpawnRequest.RequestStatus.REJECTED)

    @override_settings(ENABLE_DEFAULT_AGENT_EMAIL=True, DEFAULT_AGENT_EMAIL_DOMAIN="agents.test")
    @patch("api.services.persistent_agents.maybe_schedule_short_description", return_value=False)
    @patch("api.services.persistent_agents.maybe_schedule_mini_description", return_value=False)
    @patch("api.services.persistent_agents.maybe_schedule_agent_tags", return_value=False)
    @patch("api.services.persistent_agents.maybe_schedule_agent_avatar", return_value=False)
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_org_owner_can_approve_spawn_request(
        self,
        delay_mock,
        _avatar_mock,
        _tags_mock,
        _mini_description_mock,
        _short_description_mock,
    ):
        spawn_request = self._create_meta_gobii_spawn_request()
        self.client.force_login(self.owner)
        self._set_org_context(self.client)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/",
                data=json.dumps({"decision": "approve"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("request_status"), AgentSpawnRequest.RequestStatus.APPROVED)
        self.assertIn("pending_action_requests", payload)

        spawn_request.refresh_from_db()
        spawned_agent = spawn_request.spawned_agent
        self.assertIsNotNone(spawned_agent)
        self.assertEqual(payload.get("spawned_agent_id"), str(spawned_agent.id))
        self.assertEqual(spawned_agent.user_id, self.owner.id)
        self.assertEqual(spawned_agent.organization_id, self.org.id)
        self.assertEqual(spawned_agent.charter, spawn_request.requested_charter)
        self.assertTrue(
            PersistentAgentCommsEndpoint.objects.filter(
                owner_agent=spawned_agent,
                channel=CommsChannel.EMAIL,
                is_primary=True,
            ).exists()
        )
        self.assertTrue(
            AgentPeerLink.objects.filter(
                id=spawn_request.peer_link_id,
                agent_a=self.agent,
                agent_b=spawned_agent,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentMessage.objects.filter(
                owner_agent=self.agent,
                peer_agent=spawned_agent,
                is_outbound=True,
                body=spawn_request.handoff_message,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentMessage.objects.filter(
                owner_agent=spawned_agent,
                peer_agent=self.agent,
                is_outbound=False,
                body=spawn_request.handoff_message,
            ).exists()
        )

        step = PersistentAgentStep.objects.filter(agent=self.agent).order_by("-created_at").first()
        self.assertIsNotNone(step)
        self.assertIn("spawn request approved", step.description.lower())
        delay_mock.assert_any_call(str(spawned_agent.id), inbound_generation=ANY)
        delay_mock.assert_any_call(str(self.agent.id))

        timeline_response = self.client.get(
            f"/console/api/agents/{self.agent.id}/timeline/?direction=initial&limit=100"
        )
        self.assertEqual(timeline_response.status_code, 200)
        timeline_payload = timeline_response.json()
        self.assertFalse(
            any(
                action.get("kind") == "spawn_request"
                and action.get("requestId") == str(spawn_request.id)
                for action in timeline_payload.get("pending_action_requests", [])
            )
        )

        start_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({"is_visible": True}),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        session_key = start_response.json()["session_key"]
        heartbeat_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/heartbeat/",
            data=json.dumps({"session_key": session_key, "is_visible": True}),
            content_type="application/json",
        )
        self.assertEqual(heartbeat_response.status_code, 200)

    @override_settings(ENABLE_DEFAULT_AGENT_EMAIL=True, DEFAULT_AGENT_EMAIL_DOMAIN="agents.test")
    @patch("api.services.persistent_agents.maybe_schedule_short_description", return_value=False)
    @patch("api.services.persistent_agents.maybe_schedule_mini_description", return_value=False)
    @patch("api.services.persistent_agents.maybe_schedule_agent_tags", return_value=False)
    @patch("api.services.persistent_agents.maybe_schedule_agent_avatar", return_value=False)
    @patch(
        "api.agent.tasks.process_agent_events_task.delay",
        side_effect=RuntimeError("processing broker unavailable"),
    )
    def test_approval_response_survives_post_commit_processing_failure(
        self,
        delay_mock,
        _avatar_mock,
        _tags_mock,
        _mini_description_mock,
        _short_description_mock,
    ):
        spawn_request = self._create_meta_gobii_spawn_request()
        self.client.force_login(self.owner)
        self._set_org_context(self.client)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/",
                data=json.dumps({"decision": "approve"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        spawn_request.refresh_from_db()
        self.assertEqual(spawn_request.status, AgentSpawnRequest.RequestStatus.APPROVED)
        self.assertIsNotNone(spawn_request.spawned_agent_id)
        delay_mock.assert_called()

        timeline_response = self.client.get(
            f"/console/api/agents/{self.agent.id}/timeline/?direction=initial&limit=100"
        )
        self.assertEqual(timeline_response.status_code, 200)

        start_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/start/",
            data=json.dumps({"is_visible": True}),
            content_type="application/json",
        )
        self.assertEqual(start_response.status_code, 200)
        heartbeat_response = self.client.post(
            f"/console/api/agents/{self.agent.id}/web-sessions/heartbeat/",
            data=json.dumps(
                {
                    "session_key": start_response.json()["session_key"],
                    "is_visible": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(heartbeat_response.status_code, 200)
