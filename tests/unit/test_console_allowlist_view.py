from __future__ import annotations

from django.test import TestCase, Client, tag
from django.contrib.auth import get_user_model
from django.urls import reverse
from api.models import (
    PersistentAgent,
    BrowserUseAgent,
    Organization,
    OrganizationMembership,
    CommsAllowlistEntry,
    CommsChannel,
)


User = get_user_model()


@tag("batch_console_allowlist")
class AgentAllowlistViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="owner@example.com", email="owner@example.com", password="pw"
        )
        self.client.login(email="owner@example.com", password="pw")
        self.browser = BrowserUseAgent.objects.create(user=self.owner, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.owner, name="A", charter="c", browser_use_agent=self.browser
        )

    def _url(self):
        return reverse("agent_allowlist", kwargs={"pk": self.agent.pk})

    @tag("batch_console_allowlist")
    def test_allowlist_view_accessible_without_flag(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)

    @tag("batch_console_allowlist")
    def test_owner_access_and_add_delete_cycle_htmx(self):
        # GET should succeed
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)

        # Add invalid (email missing '@') via HTMX -> should render partial with errors and not create
        resp = self.client.post(
            self._url(),
            data={"action": "add", "channel": CommsChannel.EMAIL, "address": "bad"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(CommsAllowlistEntry.objects.filter(agent=self.agent).count(), 0)

        # Add valid entry
        resp = self.client.post(
            self._url(),
            data={"action": "add", "channel": CommsChannel.EMAIL, "address": "friend@example.com"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(CommsAllowlistEntry.objects.filter(agent=self.agent).count(), 1)

        entry = CommsAllowlistEntry.objects.get(agent=self.agent)

        # Duplicate add should not create another
        self.client.post(
            self._url(),
            data={"action": "add", "channel": CommsChannel.EMAIL, "address": "friend@example.com"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(CommsAllowlistEntry.objects.filter(agent=self.agent).count(), 1)

        # Delete via HTMX
        resp = self.client.post(
            self._url(),
            data={"action": "delete", "entry_id": str(entry.id)},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(CommsAllowlistEntry.objects.filter(agent=self.agent).count(), 0)

    @tag("batch_console_allowlist")
    def test_policy_change_updates_model(self):
        # Change to MANUAL and back to DEFAULT
        resp = self.client.post(self._url(), data={"action": "policy", "whitelist_policy": "manual"})
        self.assertEqual(resp.status_code, 302)  # redirect back to page
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.whitelist_policy, PersistentAgent.WhitelistPolicy.MANUAL)

        resp = self.client.post(self._url(), data={"action": "policy", "whitelist_policy": "default"})
        self.assertEqual(resp.status_code, 302)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.whitelist_policy, PersistentAgent.WhitelistPolicy.DEFAULT)

    @tag("batch_console_allowlist")
    def test_org_admin_access_allowed_member_forbidden(self):
        # Make agent org-owned
        org = Organization.objects.create(name="Acme", slug="acme", created_by=self.owner)
        billing = org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])
        self.agent.organization = org
        self.agent.save(update_fields=["organization"])

        # Create member and admin
        member = User.objects.create_user(username="m", email="m@example.com", password="pw")
        admin = User.objects.create_user(username="a", email="a@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=org, user=member, role=OrganizationMembership.OrgRole.MEMBER, status=OrganizationMembership.OrgStatus.ACTIVE
        )
        OrganizationMembership.objects.create(
            org=org, user=admin, role=OrganizationMembership.OrgRole.ADMIN, status=OrganizationMembership.OrgStatus.ACTIVE
        )

        # Member cannot access
        c = Client()
        c.login(email="m@example.com", password="pw")
        self.assertEqual(c.get(self._url()).status_code, 403)

        # Admin can access
        c2 = Client()
        c2.login(email="a@example.com", password="pw")
        self.assertEqual(c2.get(self._url()).status_code, 200)
