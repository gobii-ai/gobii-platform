from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, tag

from api.agent.comms.message_service import get_agent_id_from_address
from api.models import (
    CommsChannel,
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)


@tag("batch_console_agents")
class PersistentAgentCommsEndpointNormalizationTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="case-insensitive@example.com",
            email="case-insensitive@example.com",
            password="password123",
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Case Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Case Agent",
            browser_use_agent=browser_agent,
            charter="",
        )

    def test_email_endpoint_get_or_create_is_case_insensitive(self):
        first, created = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address="CaseUser@Example.Com",
            defaults={"owner_agent": None},
        )
        self.assertTrue(created)
        self.assertEqual(first.address, "caseuser@example.com")

        second, created_second = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address="caseuser@example.com",
            defaults={"owner_agent": None},
        )

        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.address, "caseuser@example.com")

    def test_non_email_endpoints_are_lowercased_and_case_insensitive(self):
        first, created = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address="User/ABC123",
            defaults={"owner_agent": None},
        )
        self.assertTrue(created)
        self.assertEqual(first.address, "user/abc123")

        second, created_second = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address="user/Abc123",
            defaults={"owner_agent": None},
        )

        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.address, "user/abc123")

    def test_agent_lookup_case_insensitive(self):
        ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="Owner.MixedCase@Example.Com",
            is_primary=True,
        )
        self.assertEqual(ep.address, "owner.mixedcase@example.com")

        found = get_agent_id_from_address(CommsChannel.EMAIL, "OWNER.MIXEDCASE@EXAMPLE.COM")
        self.assertEqual(found, self.agent.id)
