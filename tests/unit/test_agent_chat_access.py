from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, tag
from django.urls import reverse

from api.models import BrowserUseAgent, Organization, OrganizationMembership, PersistentAgent
from console.agent_chat.access import resolve_agent


@tag("batch_console_agents")
class AgentChatAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="pw",
        )
        self.client = Client()
        self.client.login(email="owner@example.com", password="pw")

        self.org = Organization.objects.create(
            name="Acme",
            slug="acme",
            plan="free",
            created_by=self.user,
        )
        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        self.personal_agent = self._create_agent("Personal Agent", organization=None)
        self.org_agent = self._create_agent("Org Agent One", organization=self.org)
        self.org_agent_two = self._create_agent("Org Agent Two", organization=self.org)
        self._set_personal_context()

    def _create_agent(self, name, organization):
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name=name)
        return PersistentAgent.objects.create(
            user=self.user,
            organization=organization,
            name=name,
            charter="",
            browser_use_agent=browser_agent,
        )

    def _set_personal_context(self):
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.username
        session.save()

    def test_resolve_agent_allows_org_agent_with_override(self):
        override = {"type": "organization", "id": str(self.org.id)}
        agent = resolve_agent(
            self.user,
            self.client.session,
            str(self.org_agent.id),
            context_override=override,
        )
        self.assertEqual(agent.id, self.org_agent.id)

    def test_resolve_agent_denies_org_agent_without_membership(self):
        User = get_user_model()
        stranger = User.objects.create_user(
            username="stranger@example.com",
            email="stranger@example.com",
            password="pw",
        )
        with self.assertRaises(PermissionDenied):
            resolve_agent(stranger, {}, str(self.org_agent.id))

    def test_roster_uses_org_agents_for_active_org_agent(self):
        url = reverse("console_agent_roster")
        response = self.client.get(
            url,
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.org.id),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        roster_ids = {entry["id"] for entry in payload.get("agents", [])}
        self.assertIn(str(self.org_agent.id), roster_ids)
        self.assertIn(str(self.org_agent_two.id), roster_ids)
        self.assertNotIn(str(self.personal_agent.id), roster_ids)

    def test_roster_includes_mini_and_short_descriptions(self):
        self.org_agent.mini_description = "Revenue pipeline assistant"
        self.org_agent.short_description = "Qualifies inbound leads and drafts handoff-ready summaries."
        self.org_agent.save(update_fields=["mini_description", "short_description"])

        response = self.client.get(
            reverse("console_agent_roster"),
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.org.id),
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        matching_entry = next(
            entry for entry in payload.get("agents", []) if entry.get("id") == str(self.org_agent.id)
        )
        self.assertEqual(matching_entry.get("mini_description"), "Revenue pipeline assistant")
        self.assertEqual(
            matching_entry.get("short_description"),
            "Qualifies inbound leads and drafts handoff-ready summaries.",
        )

    def test_roster_includes_audit_url_for_staff(self):
        User = get_user_model()
        staff_user = User.objects.create_superuser(
            username="staff@example.com",
            email="staff@example.com",
            password="pw",
        )
        staff_client = Client()
        staff_client.login(email="staff@example.com", password="pw")

        browser_agent = BrowserUseAgent.objects.create(user=staff_user, name="Staff Agent")
        persistent_agent = PersistentAgent.objects.create(
            user=staff_user,
            name="Staff Agent",
            charter="",
            browser_use_agent=browser_agent,
        )

        response = staff_client.get(
            reverse("console_agent_roster"),
            HTTP_X_GOBII_CONTEXT_TYPE="personal",
            HTTP_X_GOBII_CONTEXT_ID=str(staff_user.id),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        matching_entry = next(
            entry for entry in payload.get("agents", []) if entry.get("id") == str(persistent_agent.id)
        )
        self.assertEqual(
            matching_entry.get("audit_url"),
            f"/console/staff/agents/{persistent_agent.id}/audit/",
        )
