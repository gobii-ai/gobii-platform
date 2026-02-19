import json
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

from api.models import (
    AgentSpawnRequest,
    BrowserUseAgent,
    Organization,
    OrganizationMembership,
    PersistentAgent,
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
            requested_name="Pricing Specialist",
            requested_charter="Own competitive pricing monitoring and weekly deltas.",
            handoff_message="Take over pricing analysis and send me the first summary.",
        )

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

    @patch("console.api_views.process_agent_events_task.delay")
    @patch("api.models.AgentSpawnRequest.approve")
    def test_org_owner_can_approve_spawn_request(self, approve_mock, delay_mock):
        spawn_request = self._create_spawn_request()
        self.client.force_login(self.owner)
        self._set_org_context(self.client)

        approve_mock.return_value = (
            SimpleNamespace(id=uuid4(), name="Legal Specialist"),
            SimpleNamespace(id=uuid4()),
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/console/api/agents/{self.agent.id}/spawn-requests/{spawn_request.id}/decision/",
                data=json.dumps({"decision": "approve"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("request_status"), AgentSpawnRequest.RequestStatus.APPROVED)
        self.assertEqual(payload.get("spawned_agent_name"), "Legal Specialist")

        step = PersistentAgentStep.objects.filter(agent=self.agent).order_by("-created_at").first()
        self.assertIsNotNone(step)
        self.assertIn("spawn request approved", step.description.lower())
        delay_mock.assert_called_once_with(str(self.agent.id))
