from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from api.models import (
    AgentFileSpace,
    AgentPeerLink,
    AgentTransferInvite,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEmailEndpoint,
    PersistentAgentMessage,
    PersistentAgentWebSession,
    UserQuota,
)
from api.services.agent_transfer import AgentTransferService


User = get_user_model()


def _create_browser(user: User, name: str) -> BrowserUseAgent:
    return BrowserUseAgent.objects.create(user=user, name=name)


@tag('batch_agent_transfer')
class AgentTransferServiceTests(TestCase):
    def setUp(self) -> None:
        self.analytics_patcher = patch('util.analytics.Analytics.track_event')
        self.analytics_patcher.start()
        self.process_events_patcher = patch('api.services.agent_transfer.process_agent_events_task')
        self.process_events_mock = self.process_events_patcher.start()
        self.process_events_mock.delay.reset_mock()
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

    def _send_transfer_invite_via_console(self, email: str = "new-owner@example.com", message: str = "Please take it over.") -> AgentTransferInvite:
        self.client.login(username="owner", password="pw")
        url = reverse('agent_detail', args=[self.agent.id])
        response = self.client.post(
            url,
            {
                'action': 'transfer_agent',
                'transfer_email': email,
                'transfer_message': message,
                'name': self.agent.name,
                'charter': self.agent.charter,
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        return AgentTransferInvite.objects.get(agent=self.agent)

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
        self.process_events_mock.delay.assert_called_once_with(str(self.agent.id))
        self.process_events_mock.delay.reset_mock()

    def test_accept_transfer_syncs_email_display_name(self):
        recipient_browser = _create_browser(self.recipient, "Recipient Browser")
        recipient_agent = PersistentAgent.objects.create(
            user=self.recipient,
            name="Primary Agent",
            charter="Recipient agent",
            browser_use_agent=recipient_browser,
        )
        AgentFileSpace.objects.filter(
            owner_user=self.recipient,
            name=f"{recipient_agent.name} Files",
        ).update(name="Recipient Default Files")

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent-primary@example.com",
            is_primary=True,
        )
        email_meta = PersistentAgentEmailEndpoint.objects.create(
            endpoint=endpoint,
            display_name=self.agent.name,
        )

        invite = self._initiate(self.recipient.email)
        AgentTransferService.accept_invite(invite, self.recipient)

        self.agent.refresh_from_db()
        email_meta.refresh_from_db()

        self.assertNotEqual(self.agent.name, "Primary Agent")
        self.assertEqual(email_meta.display_name, self.agent.name)

    def test_accept_transfer_pauses_agent_when_no_capacity(self):
        UserQuota.objects.filter(user=self.recipient).update(agent_limit=1)
        existing_browser = _create_browser(self.recipient, "Existing Browser")
        PersistentAgent.objects.create(
            user=self.recipient,
            name="Existing Persistent",
            charter="",
            browser_use_agent=existing_browser,
        )

        invite = self._initiate(self.recipient.email)
        AgentTransferService.accept_invite(invite, self.recipient)

        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_active)
        self.process_events_mock.delay.assert_called_once_with(str(self.agent.id))
        self.process_events_mock.delay.reset_mock()

    def test_transfer_invitation_email_sent(self):
        invite = self._send_transfer_invite_via_console()
        self.assertEqual(invite.to_email, 'new-owner@example.com')

        self.assertEqual(len(mail.outbox), 1)
        outbound = mail.outbox[0]
        self.assertIn(self.agent.name, outbound.subject)
        self.assertEqual(outbound.to, ['new-owner@example.com'])
        self.process_events_mock.delay.assert_not_called()
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=self.agent).count(), 0)

    def test_accept_transfer_notifies_initiator(self):
        invite = self._send_transfer_invite_via_console(email=self.recipient.email)
        mail.outbox.clear()
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=self.agent).count(), 0)

        self.client.logout()
        self.client.login(username="recipient", password="pw")
        response = self.client.post(
            reverse('console-agent-transfer-invite', args=[invite.id, 'accept'])
        )
        self.assertEqual(response.status_code, 302)

        invite.refresh_from_db()
        self.assertEqual(invite.status, AgentTransferInvite.Status.ACCEPTED)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, [self.owner.email])
        self.assertIn('accepted', msg.subject.lower())
        self.assertIn(self.agent.name, msg.subject)
        messages = PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=False)
        self.assertEqual(messages.count(), 1)
        body = messages.first().body.lower()
        self.assertIn('taking over as your owner', body)
        self.process_events_mock.delay.assert_called_once_with(str(invite.agent.id))
        self.process_events_mock.delay.reset_mock()

    def test_decline_transfer_notifies_initiator(self):
        invite = self._send_transfer_invite_via_console(email=self.recipient.email)
        mail.outbox.clear()

        self.client.logout()
        self.client.login(username="recipient", password="pw")
        response = self.client.post(
            reverse('console-agent-transfer-invite', args=[invite.id, 'decline'])
        )
        self.assertEqual(response.status_code, 302)

        invite.refresh_from_db()
        self.assertEqual(invite.status, AgentTransferInvite.Status.DECLINED)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, [self.owner.email])
        self.assertIn('declined', msg.subject.lower())
        self.assertIn(self.agent.name, msg.subject)
        self.process_events_mock.delay.assert_not_called()
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=self.agent).count(), 0)

    def tearDown(self) -> None:
        self.analytics_patcher.stop()
        self.process_events_patcher.stop()
