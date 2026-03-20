from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings, tag

from api.agent.comms.message_service import _get_rejected_attachment_channel, _save_attachments
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
)


@tag("batch_email")
class SignatureAttachmentFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="sigfilter@example.com",
            email="sigfilter@example.com",
            password="secret",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Sig Filter Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Sig Filter Agent",
            charter="filter attachments",
            browser_use_agent=cls.browser_agent,
        )
        cls.endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=cls.agent,
            channel="email",
            address="sigfilter@example.com",
        )

    def _make_message(self) -> PersistentAgentMessage:
        return PersistentAgentMessage.objects.create(
            from_endpoint=self.endpoint,
            to_endpoint=self.endpoint,
            is_outbound=False,
            owner_agent=self.agent,
            body="Hello",
        )

    def test_skips_outlook_signature_image_attachment(self):
        message = self._make_message()
        attachment = ContentFile(b"signature", name="Outlook-1234.png")
        attachment.content_type = "image/png"

        _save_attachments(message, [attachment])

        self.assertEqual(message.attachments.count(), 0)

    def test_keeps_non_signature_image_attachment(self):
        message = self._make_message()
        attachment = ContentFile(b"photo", name="photo.png")
        attachment.content_type = "image/png"

        _save_attachments(message, [attachment])

        self.assertEqual(message.attachments.count(), 1)

    @override_settings(MAX_FILE_SIZE=5)
    def test_records_rejected_attachment_metadata_for_oversize_inbound_file(self):
        message = self._make_message()
        attachment = ContentFile(b"hello-bytes", name="report.pdf")
        attachment.content_type = "application/pdf"

        _save_attachments(message, [attachment])
        message.refresh_from_db()

        self.assertEqual(message.attachments.count(), 0)
        self.assertEqual(
            message.raw_payload.get("rejected_attachments"),
            [
                {
                    "filename": "report.pdf",
                    "limit_bytes": 5,
                    "reason_code": "too_large",
                    "channel": "email",
                    "size_bytes": len(b"hello-bytes"),
                }
            ],
        )

    def test_rejected_attachment_channel_falls_back_when_from_endpoint_missing(self):
        message = SimpleNamespace(
            from_endpoint=None,
            to_endpoint=SimpleNamespace(channel="email"),
            conversation=SimpleNamespace(channel="web"),
        )

        self.assertEqual(_get_rejected_attachment_channel(message), "email")
