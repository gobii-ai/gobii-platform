from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    AgentFileSpace,
    AgentPeerLink,
    AgentTransferInvite,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentWebSession,
    UserQuota,
)
from api.services.agent_transfer import AgentTransferService


User = get_user_model()


def _create_browser(user: User, name: str) -> BrowserUseAgent:
    return BrowserUseAgent.objects.create(user=user, name=name)


@tag('agent_transfer_batch')
class AgentTransferServiceTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="pw",
        )
        self.recipient = User.objects.create_user(
            username="recipient",
            email="recipient@example.com",
            password="pw",
        )
        UserQuota.objects.update_or_create(user=self.owner, defaults={"agent_limit": 5})
        UserQuota.objects.update_or_create(user=self.recipient, defaults={"agent_limit": 5})

        self.owner_browser = _create_browser(self.owner, "Owner Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Primary Agent",
            charter="Assist the owner",
            browser_use_agent=self.owner_browser,
        )

        owner_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.owner.email,
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = owner_endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        self.peer_browser = _create_browser(self.owner, "Peer Browser")
        self.peer_agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Peer Agent",
            charter="Peer",
            browser_use_agent=self.peer_browser,
        )
        AgentPeerLink.objects.create(agent_a=self.agent, agent_b=self.peer_agent, created_by=self.owner)

        self.web_session = PersistentAgentWebSession.objects.create(
            agent=self.agent,
            user=self.owner,
        )

    def _initiate(self, email: str) -> AgentTransferInvite:
        return AgentTransferService.initiate_transfer(self.agent, email, self.owner)

    def test_initiate_transfer_replaces_existing_invite(self):
        first = self._initiate("first@example.com")
        self.assertEqual(first.status, AgentTransferInvite.Status.PENDING)

        second = self._initiate("second@example.com")
        first.refresh_from_db()
        self.assertEqual(first.status, AgentTransferInvite.Status.CANCELLED)
        self.assertEqual(second.status, AgentTransferInvite.Status.PENDING)
        self.assertEqual(second.to_email, "second@example.com")

    def test_accept_transfer_migrates_agent_resources(self):
        invite = self._initiate(self.recipient.email)
        AgentTransferService.accept_invite(invite, self.recipient)

        self.agent.refresh_from_db()
        self.owner_browser.refresh_from_db()

        self.assertEqual(self.agent.user, self.recipient)
        self.assertIsNone(self.agent.organization)
        self.assertEqual(self.owner_browser.user, self.recipient)
        self.assertEqual(self.agent.preferred_contact_endpoint.address, self.recipient.email)
        self.assertTrue(self.agent.is_active)
        self.assertFalse(AgentPeerLink.objects.exists())

        filespace_ids = list(
            AgentFileSpace.objects.filter(agents=self.agent).values_list("id", flat=True)
        )
        self.assertTrue(filespace_ids)
        self.assertTrue(
            AgentFileSpace.objects.filter(id__in=filespace_ids, owner_user=self.recipient).exists()
        )

        self.web_session.refresh_from_db()
        self.assertIsNotNone(self.web_session.ended_at)

        invite.refresh_from_db()
        self.assertEqual(invite.status, AgentTransferInvite.Status.ACCEPTED)
        self.assertIsNotNone(invite.accepted_at)

    def test_accept_transfer_pauses_agent_when_no_capacity(self):
        UserQuota.objects.filter(user=self.recipient).update(agent_limit=1)
        _create_browser(self.recipient, "Existing Browser")

        invite = self._initiate(self.recipient.email)
        AgentTransferService.accept_invite(invite, self.recipient)

        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_active)

    def test_transfer_invitation_email_sent(self):
        self.client.login(username="owner", password="pw")

        url = reverse('agent_detail', args=[self.agent.id])
        response = self.client.post(
            url,
            {
                'action': 'transfer_agent',
                'transfer_email': 'new-owner@example.com',
                'transfer_message': 'Please take it over.',
                'name': self.agent.name,
                'charter': self.agent.charter,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)

        invite = AgentTransferInvite.objects.get(agent=self.agent)
        self.assertEqual(invite.to_email, 'new-owner@example.com')

        self.assertEqual(len(mail.outbox), 1)
        outbound = mail.outbox[0]
        self.assertIn(self.agent.name, outbound.subject)
        self.assertEqual(outbound.to, ['new-owner@example.com'])
