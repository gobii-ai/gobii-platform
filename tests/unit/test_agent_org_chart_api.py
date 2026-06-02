import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import (
    AgentCollaborator,
    AgentOrgChart,
    AgentPeerLink,
    BrowserUseAgent,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentConversation,
)


@tag("batch_agent_chat")
class AgentOrgChartApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="org-chart-owner",
            email="org-chart-owner@example.com",
            password="password123",
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("console_agent_org_chart")

    def _agent(self, name, user=None, organization=None):
        owner = user or self.user
        browser_agent = BrowserUseAgent.objects.create(user=owner, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=owner,
            organization=organization,
            name=name,
            charter=f"{name} charter",
            browser_use_agent=browser_agent,
        )

    def _put(self, payload, client=None, url=None):
        return (client or self.client).put(
            url or self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_default_empty_chart_response(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["context"]["type"], "personal")
        self.assertEqual(payload["edges"], [])
        self.assertEqual(payload["nodes"], [])
        self.assertEqual(payload["unplaced_peer_links"], [])

    def test_put_creates_peer_link_for_manager_child_edge(self):
        manager = self._agent("Manager")
        worker = self._agent("Worker")
        revision = self.client.get(self.url).json()["revision"]

        response = self._put(
            {
                "revision": revision,
                "viewport": {"x": 1, "y": 2, "zoom": 0.8},
                "nodes": [
                    {"agentId": str(manager.id), "x": 10, "y": 20},
                    {"agentId": str(worker.id), "x": 30, "y": 120},
                ],
                "edges": [
                    {"parentAgentId": str(manager.id), "childAgentId": str(worker.id)},
                ],
            }
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["edges"]), 1)
        link = AgentPeerLink.objects.get()
        self.assertEqual(link.agent_a_id, manager.id)
        self.assertEqual(link.agent_b_id, worker.id)
        self.assertEqual(link.created_by_id, self.user.id)

    def test_put_removes_peer_link_when_chart_edge_deleted_and_preserves_conversation(self):
        manager = self._agent("Manager")
        worker = self._agent("Worker")
        create_revision = self.client.get(self.url).json()["revision"]
        create_response = self._put(
            {
                "revision": create_revision,
                "nodes": [
                    {"agentId": str(manager.id), "x": 10, "y": 20},
                    {"agentId": str(worker.id), "x": 30, "y": 120},
                ],
                "edges": [
                    {"parentAgentId": str(manager.id), "childAgentId": str(worker.id)},
                ],
            }
        )
        self.assertEqual(create_response.status_code, 200)
        link = AgentPeerLink.objects.get()
        conversation = PersistentAgentConversation.objects.create(
            channel="other",
            address="peer-dm:test",
            owner_agent=manager,
            is_peer_dm=True,
            peer_link=link,
        )

        delete_response = self._put(
            {
                "revision": create_response.json()["revision"],
                "nodes": [
                    {"agentId": str(manager.id), "x": 10, "y": 20},
                    {"agentId": str(worker.id), "x": 30, "y": 120},
                ],
                "edges": [],
            }
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(AgentPeerLink.objects.filter(id=link.id).exists())
        conversation.refresh_from_db()
        self.assertIsNone(conversation.peer_link_id)
        self.assertFalse(conversation.is_peer_dm)

    def test_put_rejects_second_parent_for_child(self):
        manager = self._agent("Manager")
        second_manager = self._agent("Second Manager")
        worker = self._agent("Worker")
        revision = self.client.get(self.url).json()["revision"]

        response = self._put(
            {
                "revision": revision,
                "nodes": [],
                "edges": [
                    {"parentAgentId": str(manager.id), "childAgentId": str(worker.id)},
                    {"parentAgentId": str(second_manager.id), "childAgentId": str(worker.id)},
                ],
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentPeerLink.objects.exists())

    def test_put_rejects_cycle(self):
        first = self._agent("First")
        second = self._agent("Second")
        revision = self.client.get(self.url).json()["revision"]

        response = self._put(
            {
                "revision": revision,
                "nodes": [],
                "edges": [
                    {"parentAgentId": str(first.id), "childAgentId": str(second.id)},
                    {"parentAgentId": str(second.id), "childAgentId": str(first.id)},
                ],
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentPeerLink.objects.exists())

    def test_put_rejects_cross_context_agent(self):
        manager = self._agent("Manager")
        other_user = get_user_model().objects.create_user(
            username="other-owner",
            email="other-owner@example.com",
            password="password123",
        )
        outsider = self._agent("Outsider", user=other_user)
        revision = self.client.get(self.url).json()["revision"]

        response = self._put(
            {
                "revision": revision,
                "nodes": [],
                "edges": [
                    {"parentAgentId": str(manager.id), "childAgentId": str(outsider.id)},
                ],
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentPeerLink.objects.exists())

    def test_put_rejects_collaborator_only_mutation(self):
        owner = get_user_model().objects.create_user(
            username="shared-owner",
            email="shared-owner@example.com",
            password="password123",
        )
        first = self._agent("Shared One", user=owner)
        second = self._agent("Shared Two", user=owner)
        AgentCollaborator.objects.create(agent=first, user=self.user)
        AgentCollaborator.objects.create(agent=second, user=self.user)
        revision = self.client.get(self.url).json()["revision"]

        response = self._put(
            {
                "revision": revision,
                "nodes": [],
                "edges": [
                    {"parentAgentId": str(first.id), "childAgentId": str(second.id)},
                ],
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentPeerLink.objects.exists())

    def test_get_imports_existing_peer_links_and_reports_conflicts_unplaced(self):
        manager = self._agent("Manager")
        worker = self._agent("Worker")
        second_manager = self._agent("Second Manager")
        imported = AgentPeerLink.objects.create(
            agent_a=manager,
            agent_b=worker,
            created_by=self.user,
        )
        conflicted = AgentPeerLink.objects.create(
            agent_a=second_manager,
            agent_b=worker,
            created_by=self.user,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [(edge["parentAgentId"], edge["childAgentId"]) for edge in payload["edges"]],
            [(str(manager.id), str(worker.id))],
        )
        self.assertEqual(payload["edges"][0]["peerLinkId"], str(imported.id))
        self.assertEqual(payload["unplaced_peer_links"][0]["peerLinkId"], str(conflicted.id))

    def test_put_rejects_stale_revision(self):
        self._agent("Manager")
        self.client.get(self.url)
        chart = AgentOrgChart.objects.get(owner_user=self.user)
        chart.revision += 1
        chart.save(update_fields=["revision", "updated_at"])

        response = self._put(
            {
                "revision": 1,
                "nodes": [],
                "edges": [],
            }
        )

        self.assertEqual(response.status_code, 409)

    def test_org_member_without_manage_role_cannot_update_chart(self):
        org = Organization.objects.create(
            name="Org",
            slug="org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.MEMBER,
        )
        url = f"{self.url}?context_type=organization&context_id={org.id}"
        revision = self.client.get(url).json()["revision"]

        response = self._put(
            {
                "revision": revision,
                "nodes": [],
                "edges": [],
            },
            url=url,
        )

        self.assertEqual(response.status_code, 403)
