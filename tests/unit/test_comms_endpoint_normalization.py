from django.test import TransactionTestCase, tag

from api.models import CommsChannel, PersistentAgentCommsEndpoint


@tag("batch_console_agents")
class PersistentAgentCommsEndpointNormalizationTests(TransactionTestCase):
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
