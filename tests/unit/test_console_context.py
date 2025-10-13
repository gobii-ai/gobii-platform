import uuid

from django.urls import reverse
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from waffle.models import Flag

from api.models import (
    Organization,
    OrganizationMembership,
    BrowserUseAgent,
    PersistentAgent,
    BrowserUseAgentTask,
    TaskCredit,
    OrganizationInvite,
    ProxyServer,
    DedicatedProxyAllocation,
)
from django.utils import timezone
from constants.plans import PlanNamesChoices


User = get_user_model()


@override_settings(
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    },
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)

@tag('batch_console_context')
class ConsoleContextTests(TestCase):
    def setUp(self):
        # Enable organizations feature flag for all requests
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        # Users
        self.owner = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
        self.stranger = User.objects.create_user(username="stranger", email="stranger@example.com", password="pw")

        # Org and membership
        self.org = Organization.objects.create(
            name="Acme, Inc.", slug="acme", plan="free", created_by=self.owner
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.subscription = PlanNamesChoices.ORG_TEAM.value
        billing.save(update_fields=["purchased_seats", "subscription"])
        owner_billing = self.owner.billing
        owner_billing.subscription = PlanNamesChoices.STARTUP.value
        owner_billing.save(update_fields=["subscription"])

        # Agents
        self.personal_browser = BrowserUseAgent.objects.create(user=self.owner, name="Personal Agent")
        self.personal_agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=None,
            name="Personal PA",
            charter="",
            browser_use_agent=self.personal_browser,
        )

        self.org_browser = BrowserUseAgent.objects.create(user=self.owner, name="Org Agent")
        self.org_agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=self.org,
            name="Org PA",
            charter="",
            browser_use_agent=self.org_browser,
        )

        # Ensure the organization has credits so org tasks can be created
        TaskCredit.objects.create(
            organization=self.org,
            credits=10,
            credits_used=0,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timezone.timedelta(days=30),
        )

        # Tasks: one personal, one org-owned, one agent-less
        self.personal_task = BrowserUseAgentTask.objects.create(user=self.owner, agent=self.personal_browser)
        self.org_task = BrowserUseAgentTask.objects.create(user=self.owner, agent=self.org_browser)
        self.agentless_task = BrowserUseAgentTask.objects.create(user=self.owner, agent=None)

        # Login owner by default
        assert self.client.login(username="owner", password="pw")

    def _set_personal_context(self):
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.owner.id)
        session["context_name"] = self.owner.get_full_name() or self.owner.username
        session.save()

    def _set_org_context(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def test_tasks_view_personal_excludes_org_owned(self):
        self._set_personal_context()
        url = reverse("tasks")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        tasks = list(resp.context["tasks"])  # paginated page object
        ids = {t.id for t in tasks}
        self.assertIn(self.personal_task.id, ids)
        self.assertIn(self.agentless_task.id, ids)
        self.assertNotIn(self.org_task.id, ids)

    def test_tasks_view_org_requires_membership_and_shows_org_tasks(self):
        # As owner (member) — should see org tasks
        self._set_org_context()
        url = reverse("tasks")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        tasks = list(resp.context["tasks"])  # paginated page object
        ids = {t.id for t in tasks}
        self.assertIn(self.org_task.id, ids)
        # Switch to stranger (no membership) — should be forbidden
        self.client.logout()
        assert self.client.login(username="stranger", password="pw")
        self._set_org_context()
        resp2 = self.client.get(url)
        self.assertEqual(resp2.status_code, 403)

    def test_agent_detail_scoping(self):
        # Personal context: org-owned agent should 404
        self._set_personal_context()
        url = reverse("agent_detail", kwargs={"pk": self.org_agent.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
        # Org context with membership: should 200
        self._set_org_context()
        resp2 = self.client.get(url)
        self.assertEqual(resp2.status_code, 200)

    def test_org_detail_sets_console_context(self):
        # Visiting org detail should set session context to organization
        url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(self.org.id))
        self.assertEqual(session.get("context_name"), self.org.name)

    def test_leaving_org_resets_context_to_personal(self):
        # Add a second owner so the original owner can leave
        another = User.objects.create_user(username="other", email="other@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=self.org,
            user=another,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self._set_org_context()
        leave_url = reverse("org_leave_org", kwargs={"org_id": self.org.id})
        resp = self.client.post(leave_url, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Verify membership updated
        mem = OrganizationMembership.objects.get(org=self.org, user=self.owner)
        self.assertEqual(mem.status, OrganizationMembership.OrgStatus.REMOVED)
        # Session reset to personal
        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

    def test_header_menu_reflects_context(self):
        # Organization context should show Organization link and hide Profile
        self._set_org_context()
        resp = self.client.get(reverse("console-home"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(str(self.org.id), html)
        self.assertIn("Organization", html)
        # Switch to personal context
        self._set_personal_context()
        resp2 = self.client.get(reverse("console-home"))
        self.assertEqual(resp2.status_code, 200)
        html2 = resp2.content.decode()
        self.assertIn("Profile", html2)

    def test_sidebar_nav_reflects_context(self):
        # Org context: sidebar should show Organization link
        self._set_org_context()
        resp = self.client.get(reverse("agents"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("Organization", content)
        # Personal context: sidebar should show Profile link
        self._set_personal_context()
        resp2 = self.client.get(reverse("agents"))
        self.assertEqual(resp2.status_code, 200)
        content2 = resp2.content.decode()
        self.assertIn("Profile", content2)

    def test_agent_detail_includes_dedicated_ip_counts(self):
        self._set_personal_context()
        proxy = ProxyServer.objects.create(
            name="Dedicated Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="dedicated.example.com",
            port=8080,
            username="dedicated",
            password="secret",
            static_ip="203.0.113.5",
            is_active=True,
            is_dedicated=True,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(proxy, self.owner)

        url = reverse("agent_detail", kwargs={"pk": self.personal_agent.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('data-dedicated-ip-total="1"', html)
        self.assertIn('name="dedicated_proxy_id"', html)
        self.assertIn('203.0.113.5', html)
        self.assertIn('Use shared proxy pool', html)
        self.assertIn('Remove', html)

    def test_billing_query_switches_to_org_context(self):
        self._set_personal_context()
        billing_url = f"{reverse('billing')}?org_id={self.org.id}"
        resp = self.client.get(billing_url)
        self.assertEqual(resp.status_code, 200)
        session = self.client.session
        self.assertEqual(session.get('context_type'), 'organization')
        self.assertEqual(session.get('context_id'), str(self.org.id))
        self.assertEqual(session.get('context_name'), self.org.name)

    def test_agent_contact_view_shows_org_context_banner(self):
        self._set_org_context()
        session = self.client.session
        session["agent_charter"] = "help the organization"
        session.save()

        resp = self.client.get(reverse("agent_create_contact"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="agent-owner-selector-contact"', html)
        self.assertIn(self.org.name, html)
        self.assertNotIn('disabled aria-disabled="true"', html)

    def test_agent_contact_view_blocks_member_role(self):
        membership = OrganizationMembership.objects.get(org=self.org, user=self.owner)
        membership.role = OrganizationMembership.OrgRole.MEMBER
        membership.save(update_fields=["role"])

        self._set_org_context()
        session = self.client.session
        session["agent_charter"] = "help the organization"
        session.save()

        resp = self.client.get(reverse("agent_create_contact"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="agent-owner-selector-contact"', html)
        self.assertIn("You need to be an organization owner or admin", html)
        self.assertIn('disabled aria-disabled="true"', html)

    def test_agent_contact_post_denied_for_member_role(self):
        membership = OrganizationMembership.objects.get(org=self.org, user=self.owner)
        membership.role = OrganizationMembership.OrgRole.MEMBER
        membership.save(update_fields=["role"])

        self._set_org_context()
        session = self.client.session
        session["agent_charter"] = "orchestrate research"
        session.save()

        payload = {
            "preferred_contact_method": "email",
            "contact_endpoint_email": "owner@example.com",
            "email_enabled": "on",
        }

        resp = self.client.post(reverse("agent_create_contact"), data=payload)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("You need to be an organization owner or admin", html)
        # Ensure no additional org-owned agents were created
        org_agent_count = PersistentAgent.objects.filter(organization=self.org).count()
        self.assertEqual(org_agent_count, 1)

    def test_org_invite_accept_sets_context_and_membership(self):
        # Create invite for a new user
        invitee = User.objects.create_user(username="invitee", email="invitee@example.com", password="pw")
        inv = OrganizationInvite.objects.create(
            org=self.org,
            email=invitee.email,
            role=OrganizationMembership.OrgRole.MEMBER,
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timezone.timedelta(days=7),
            invited_by=self.owner,
        )
        # Login as invitee and accept
        self.client.logout()
        assert self.client.login(username="invitee", password="pw")
        url = reverse("org_invite_accept", kwargs={"token": inv.token})
        resp = self.client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Context set to org
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(self.org.id))
        # Membership created/active
        mem = OrganizationMembership.objects.get(org=self.org, user=invitee)
        self.assertEqual(mem.status, OrganizationMembership.OrgStatus.ACTIVE)

    def test_org_invite_reject_sets_context(self):
        invitee = User.objects.create_user(username="invitee2", email="invitee2@example.com", password="pw")
        inv = OrganizationInvite.objects.create(
            org=self.org,
            email=invitee.email,
            role=OrganizationMembership.OrgRole.MEMBER,
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timezone.timedelta(days=7),
            invited_by=self.owner,
        )
        self.client.logout()
        assert self.client.login(username="invitee2", password="pw")
        url = reverse("org_invite_reject", kwargs={"token": inv.token})
        resp = self.client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Context set to org
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(self.org.id))
        # No active membership created by rejection
        self.assertFalse(OrganizationMembership.objects.filter(org=self.org, user=invitee, status=OrganizationMembership.OrgStatus.ACTIVE).exists())
