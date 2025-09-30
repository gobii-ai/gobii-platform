from django.urls import reverse
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from waffle.models import Flag

from api.models import (
    Organization,
    OrganizationMembership,
    BrowserUseAgent,
    PersistentAgent,
)


User = get_user_model()


@tag('batch_console_context')
class AgentReassignContextTests(TestCase):
    def setUp(self):
        # Ensure organizations UI/flows are enabled
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        self.user = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
        assert self.client.login(username="owner", password="pw")

        # Organization with seats and membership
        self.org = Organization.objects.create(name="Acme Org", slug="acme", created_by=self.user)
        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        # Personal agent
        self.browser = BrowserUseAgent.objects.create(user=self.user, name="Personal Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            organization=None,
            name="Personal Agent",
            charter="Help the owner",
            browser_use_agent=self.browser,
        )

    def test_reassign_sets_session_to_org(self):
        url = reverse("agent_detail", kwargs={"pk": self.agent.id})

        # AJAX post to reassign
        resp = self.client.post(
            url,
            data={"action": "reassign_org", "target_org_id": str(self.org.id)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        if resp.status_code != 200:
            print("Reassign response:", resp.status_code, getattr(resp, 'content', b'')[:500])
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))

        # Session context should be organization now
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(self.org.id))

        # Follow-up request to agent detail should succeed (agent moved to org)
        follow = self.client.get(url)
        self.assertEqual(follow.status_code, 200)

    def test_move_back_to_personal_sets_session(self):
        # First, move to org
        url = reverse("agent_detail", kwargs={"pk": self.agent.id})
        resp = self.client.post(
            url,
            data={"action": "reassign_org", "target_org_id": str(self.org.id)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)

        # Now move back to personal (no target_org_id)
        resp2 = self.client.post(
            url,
            data={"action": "reassign_org"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        if resp2.status_code != 200:
            print("Move personal response:", resp2.status_code, getattr(resp2, 'content', b'')[:500])
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2.get("success"))

        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.user.id))
