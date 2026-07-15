from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.web_streaming import resolve_web_stream_target
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    build_web_agent_address,
    build_web_user_address,
)


@tag("batch_event_processing")
class ResolveWebStreamTargetTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="stream-owner",
            email="stream-owner@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Stream Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Stream Agent",
            charter="Stream replies",
            browser_use_agent=browser_agent,
        )
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(self.agent.id),
        )
        self.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
        )

    def _inbound(self, endpoint=None):
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=endpoint or self.user_endpoint,
            body="hello",
        )

    def test_first_inbound_web_message_resolves_without_prior_outbound(self):
        self._inbound()

        target = resolve_web_stream_target(self.agent)

        self.assertIsNotNone(target)
        self.assertEqual(target.user_id, self.user.id)
        self.assertEqual(target.address, self.user_endpoint.address)

    def test_previous_email_or_sms_outbound_does_not_block_latest_web_inbound(self):
        cases = (
            (CommsChannel.EMAIL, "recipient@example.com"),
            (CommsChannel.SMS, "+15555550123"),
        )
        for channel, address in cases:
            with self.subTest(channel=channel):
                PersistentAgentMessage.objects.filter(owner_agent=self.agent).delete()
                recipient_endpoint = PersistentAgentCommsEndpoint.objects.create(
                    channel=channel,
                    address=address,
                )
                PersistentAgentMessage.objects.create(
                    owner_agent=self.agent,
                    is_outbound=True,
                    from_endpoint=self.agent_endpoint,
                    to_endpoint=recipient_endpoint,
                    body="older non-web message",
                )
                self._inbound()

                target = resolve_web_stream_target(self.agent)

                self.assertIsNotNone(target)
                self.assertEqual(target.user_id, self.user.id)

    def test_latest_outbound_web_message_uses_recipient(self):
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=self.agent_endpoint,
            to_endpoint=self.user_endpoint,
            body="reply",
        )

        target = resolve_web_stream_target(self.agent)

        self.assertIsNotNone(target)
        self.assertEqual(target.user_id, self.user.id)

    def test_latest_non_web_message_has_no_stream_target(self):
        self._inbound()
        email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="sender@example.com",
        )
        self._inbound(email_endpoint)

        self.assertIsNone(resolve_web_stream_target(self.agent))

    def test_malformed_or_mismatched_web_address_has_no_stream_target(self):
        malformed = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="not-a-web-user-address",
        )
        self._inbound(malformed)
        self.assertIsNone(resolve_web_stream_target(self.agent))

        other_browser = BrowserUseAgent.objects.create(user=self.user, name="Other Browser")
        other_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Other Agent",
            charter="Other",
            browser_use_agent=other_browser,
        )
        mismatched = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, other_agent.id),
        )
        self._inbound(mismatched)
        self.assertIsNone(resolve_web_stream_target(self.agent))
