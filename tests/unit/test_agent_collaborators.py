from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    AgentCollaborator,
    AgentCollaboratorInvite,
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    PersistentAgent,
    UserBilling,
    build_web_user_address,
)

User = get_user_model()


@tag("batch_agent_collaborators")
class AgentCollaboratorWhitelistTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        self.collaborator = User.objects.create_user(
            username="collab",
            email="collab@example.com",
        )
        AgentCollaborator.objects.create(
            agent=self.agent,
            user=self.collaborator,
            invited_by=self.owner,
        )

    def test_collaborator_email_and_web_are_allowed(self):
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.EMAIL, self.collaborator.email)
        )
        self.assertTrue(
            self.agent.is_recipient_whitelisted(CommsChannel.EMAIL, self.collaborator.email)
        )
        web_address = build_web_user_address(self.collaborator.id, self.agent.id)
        self.assertTrue(
            self.agent.is_sender_whitelisted(CommsChannel.WEB, web_address)
        )


@tag("batch_agent_collaborators")
class AgentCollaboratorInviteViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
        )
        self.invite = AgentCollaboratorInvite.objects.create(
            agent=self.agent,
            email="collab@example.com",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )

    def test_accept_view_creates_collaborator(self):
        user = User.objects.create_user(
            username="collab",
            email="collab@example.com",
            password="testpass123",
        )
        self.client.force_login(user)
        url = reverse("agent_collaborator_invite_accept", kwargs={"token": self.invite.token})

        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AgentCollaborator.objects.filter(agent=self.agent, user=user).exists()
        )
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, AgentCollaboratorInvite.InviteStatus.ACCEPTED)


@tag("batch_agent_collaborators")
class AgentCollaboratorContactLimitTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        billing, _ = UserBilling.objects.get_or_create(user=self.owner)
        billing.max_contacts_per_agent = 2
        billing.save(update_fields=["max_contacts_per_agent"])

    def test_collaborator_invite_counts_toward_limit(self):
        AgentCollaboratorInvite.objects.create(
            agent=self.agent,
            email="invite@example.com",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )

        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="contact1@example.com",
            is_active=True,
        )

        with self.assertRaises(ValidationError):
            CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address="contact2@example.com",
                is_active=True,
            )


@tag("batch_agent_collaborators")
class AgentCollaboratorInviteLimitTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        billing, _ = UserBilling.objects.get_or_create(user=self.owner)
        billing.max_contacts_per_agent = 1
        billing.save(update_fields=["max_contacts_per_agent"])

    def test_accept_invite_at_capacity_does_not_fail(self):
        invite = AgentCollaboratorInvite.objects.create(
            agent=self.agent,
            email="collab@example.com",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )
        user = User.objects.create_user(
            username="collab",
            email="collab@example.com",
        )

        collaborator = invite.accept(user)

        self.assertEqual(collaborator.user_id, user.id)
        self.assertTrue(
            AgentCollaborator.objects.filter(agent=self.agent, user=user).exists()
        )
        invite.refresh_from_db()
        self.assertEqual(invite.status, AgentCollaboratorInvite.InviteStatus.ACCEPTED)


@tag("batch_agent_collaborators")
class AgentCollaboratorInviteExpiryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
            whitelist_policy=PersistentAgent.WhitelistPolicy.MANUAL,
        )
        billing, _ = UserBilling.objects.get_or_create(user=self.owner)
        billing.max_contacts_per_agent = 1
        billing.save(update_fields=["max_contacts_per_agent"])

    def test_expired_pending_invite_does_not_block_new_invite(self):
        invite = AgentCollaboratorInvite.objects.create(
            agent=self.agent,
            email="old@example.com",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=1),
        )
        AgentCollaboratorInvite.objects.filter(pk=invite.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        followup = AgentCollaboratorInvite.objects.create(
            agent=self.agent,
            email="new@example.com",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )

        self.assertNotEqual(invite.id, followup.id)
        self.assertEqual(followup.status, AgentCollaboratorInvite.InviteStatus.PENDING)


@tag("batch_agent_collaborators")
class AgentCollaboratorReinviteTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Test Agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent,
        )
        self.collaborator = User.objects.create_user(
            username="collab",
            email="collab@example.com",
        )

    def test_reinvite_after_removal_creates_new_invite(self):
        invite = AgentCollaboratorInvite.objects.create(
            agent=self.agent,
            email=self.collaborator.email,
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )
        invite.accept(self.collaborator)
        AgentCollaborator.objects.filter(agent=self.agent, user=self.collaborator).delete()

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("agent_detail", kwargs={"pk": self.agent.id}),
            data={
                "action": "add_collaborator",
                "email": self.collaborator.email,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        invite.refresh_from_db()
        self.assertEqual(invite.status, AgentCollaboratorInvite.InviteStatus.EXPIRED)
        self.assertTrue(
            AgentCollaboratorInvite.objects.filter(
                agent=self.agent,
                email__iexact=self.collaborator.email,
                status=AgentCollaboratorInvite.InviteStatus.PENDING,
            ).exists()
        )
