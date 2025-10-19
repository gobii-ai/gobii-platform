from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, tag
from django.urls import reverse

from django.conf import settings

from api.agent.tools.sms_sender import execute_send_sms
from api.models import (
    CommsAllowlistEntry,
    CommsChannel,
    Organization,
    OrganizationMembership,
    OrganizationBilling,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentSmsEndpoint,
    PersistentAgentSmsGroup,
    BrowserUseAgent,
)
from api.webhooks import twilio_conversation_webhook


User = get_user_model()


def _make_browser_agent(user: User, name: str = "BA") -> BrowserUseAgent:
    with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("batch_sms_groups")
class AgentSmsGroupTests(TestCase):
    def setUp(self):
        self.ensure_conv_patcher = patch("util.sms.ensure_group_conversation", return_value="CHALLOW")
        self.mock_ensure_conv = self.ensure_conv_patcher.start()
        self.addCleanup(self.ensure_conv_patcher.stop)

        self.contact_cap_patcher = patch("util.subscription_helper.get_user_max_contacts_per_agent", return_value=50)
        self.contact_cap_patcher.start()
        self.addCleanup(self.contact_cap_patcher.stop)

        self.user = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="pw",
        )
        browser_agent = _make_browser_agent(self.user)
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            charter="help",
            browser_use_agent=browser_agent,
        )
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        self.sms_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15550000000",
            is_primary=True,
        )
        PersistentAgentSmsEndpoint.objects.create(
            endpoint=self.sms_endpoint,
            supports_mms=True,
            carrier_name="TestCarrier",
        )

    def _add_sms_contact(self, number: str) -> CommsAllowlistEntry:
        return CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address=number,
            allow_inbound=True,
            allow_outbound=True,
        )

    @patch("api.agent.tools.sms_sender.deliver_agent_group_sms")
    def test_execute_send_sms_group_success(self, deliver_group):
        numbers = ["+15550000001", "+15550000002"]
        for number in numbers:
            self._add_sms_contact(number)

        group = self.agent.sms_groups.get(name=PersistentAgentSmsGroup.ALLOWLIST_GROUP_NAME)

        self.mock_ensure_conv.reset_mock()
        self.mock_ensure_conv.return_value = "CHXXXX"

        response = execute_send_sms(
            self.agent,
            {"group_id": str(group.id), "body": "Launch update"},
        )

        self.assertEqual(response["status"], "ok")
        self.mock_ensure_conv.assert_called_with(group, proxy_number=self.sms_endpoint.address)
        self.assertEqual(PersistentAgentMessage.objects.count(), 1)
        message = PersistentAgentMessage.objects.first()
        self.assertIsNotNone(message.conversation)
        self.assertEqual(message.conversation.address, "CHXXXX")
        deliver_group.assert_called_once_with(message, group)

    def test_sms_allowlist_limit_enforced(self):
        max_members = PersistentAgentSmsGroup.MAX_MEMBERS
        for index in range(max_members):
            number = f"+1202555{index:04d}"
            self._add_sms_contact(number)

        with self.assertRaises(ValidationError):
            self._add_sms_contact("+13125550100")

        group = self.agent.sms_groups.get(name=PersistentAgentSmsGroup.ALLOWLIST_GROUP_NAME)
        members = list(group.members.order_by("phone_number"))
        self.assertEqual(len(members), max_members)

        allowlist_numbers = set(
            self.agent.manual_allowlist.filter(channel=CommsChannel.SMS).values_list("address", flat=True)
        )
        self.assertEqual(allowlist_numbers, {member.phone_number for member in members})

    def test_sms_allowlist_permits_org_agents(self):
        org = Organization.objects.create(name="Org", created_by=self.user)
        billing = getattr(org, "billing", None)
        if billing is None:
            OrganizationBilling.objects.create(organization=org, purchased_seats=1)
        else:
            billing.purchased_seats = 1
            billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        agent = PersistentAgent.objects.create(
            user=self.user,
            organization=org,
            name="Org Agent",
            charter="help",
            browser_use_agent=_make_browser_agent(self.user, "Org BA"),
        )

        entry = CommsAllowlistEntry.objects.create(
            agent=agent,
            channel=CommsChannel.SMS,
            address="+15550009999",
        )

        self.assertTrue(entry.is_active)


@tag("batch_sms_groups")
class TwilioConversationWebhookTests(TestCase):
    def setUp(self):
        self.process_events_patcher = patch("api.agent.tasks.process_agent_events_task.delay")
        self.addCleanup(self.process_events_patcher.stop)
        self.mock_process_events = self.process_events_patcher.start()

        self.ensure_conv_patcher = patch("util.sms.ensure_group_conversation", return_value="CH123")
        self.mock_ensure_conv = self.ensure_conv_patcher.start()
        self.addCleanup(self.ensure_conv_patcher.stop)

        self.contact_cap_patcher = patch("util.subscription_helper.get_user_max_contacts_per_agent", return_value=50)
        self.contact_cap_patcher.start()
        self.addCleanup(self.contact_cap_patcher.stop)

        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="u@example.com",
            email="u@example.com",
            password="pw",
        )
        browser_agent = _make_browser_agent(self.user, "SMS BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SMS Agent",
            charter="sms",
            browser_use_agent=browser_agent,
        )
        self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        self.agent.save(update_fields=["whitelist_policy"])
        self.sms_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15550001000",
            is_primary=True,
        )
        PersistentAgentSmsEndpoint.objects.create(
            endpoint=self.sms_endpoint,
            supports_mms=True,
            carrier_name="Carrier",
        )

        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15550001001",
            allow_inbound=True,
            allow_outbound=True,
        )

        self.group = self.agent.sms_groups.get(name=PersistentAgentSmsGroup.ALLOWLIST_GROUP_NAME)
        if self.group.twilio_conversation_sid != "CH123":
            self.group.twilio_conversation_sid = "CH123"
            self.group.save(update_fields=["twilio_conversation_sid", "updated_at"])

        PersistentAgentConversation.objects.create(
            channel=CommsChannel.SMS,
            address="CH123",
            owner_agent=self.agent,
            sms_group=self.group,
        )

    def test_webhook_ingests_conversation_message(self):
        data = {
            "EventType": "onMessageAdded",
            "ConversationSid": "CH123",
            "Author": "+15550001001",
            "Body": "Hi there",
            "MessagingBinding.Address": "+15550001001",
            "MessagingBinding.ProxyAddress": self.sms_endpoint.address,
            "MediaCount": "0",
        }
        request = self.factory.post(
            reverse("api:sms_conversation_webhook") + f"?t={settings.TWILIO_INCOMING_WEBHOOK_TOKEN}",
            data,
        )

        response = twilio_conversation_webhook(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PersistentAgentMessage.objects.filter(conversation__address="CH123").count(), 1)
        message = PersistentAgentMessage.objects.get(conversation__address="CH123")
        self.assertFalse(message.is_outbound)
        self.assertEqual(message.body, "Hi there")
        self.assertEqual(message.owner_agent, self.agent)
