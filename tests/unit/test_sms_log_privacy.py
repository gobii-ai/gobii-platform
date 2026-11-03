import logging
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings, tag

from api.agent.comms.outbound_delivery import _convert_sms_body_to_plaintext
from util import sms


TO_NUMBER = "+14155552671"
FROM_NUMBER = "+12025550123"
SECRET_BODY = "Your lab results are back: cholesterol 212. Call Dr. Rivera at 555-0199."


def _info_output(records):
    """Everything logged at INFO or above, as it would reach a default deployment's stdout."""
    return "\n".join(record.getMessage() for record in records if record.levelno >= logging.INFO)


@tag("batch_sms")
@override_settings(
    TWILIO_ENABLED=True,
    TWILIO_ACCOUNT_SID="AC00000000000000000000000000000000",
    TWILIO_AUTH_TOKEN="test-token",
    TWILIO_MESSAGING_SERVICE_SID="MG00000000000000000000000000000000",
)
class SendSmsLogPrivacyTests(TestCase):
    def _client(self, mock_client_cls, error=None):
        client = Mock()
        if error is not None:
            client.messages.create.side_effect = error
        else:
            client.messages.create.return_value = Mock(sid="SM123")
        mock_client_cls.return_value = client
        return client

    @patch("util.sms.Client")
    def test_successful_send_logs_masked_numbers_and_length_only(self, mock_client_cls):
        client = self._client(mock_client_cls)

        with self.assertLogs("util.sms", level="DEBUG") as logs:
            result = sms.send_sms(to_number=TO_NUMBER, from_number=FROM_NUMBER, body=SECRET_BODY)

        self.assertEqual(result, "SM123")
        # The message still goes out unchanged; only the log lines change.
        self.assertEqual(client.messages.create.call_args.kwargs["body"], SECRET_BODY)
        self.assertEqual(client.messages.create.call_args.kwargs["to"], TO_NUMBER)
        output = "\n".join(record.getMessage() for record in logs.records)
        self.assertNotIn(TO_NUMBER, output)
        self.assertNotIn(FROM_NUMBER, output)
        self.assertNotIn("cholesterol", output)
        self.assertIn("***2671", output)
        self.assertIn("***0123", output)
        self.assertIn(f"({len(SECRET_BODY)} chars)", output)
        self.assertIn("SM123", output)

    @patch("util.sms.Client")
    def test_failed_send_masks_the_number_quoted_in_the_error(self, mock_client_cls):
        self._client(mock_client_cls, error=Exception(f"The 'To' number {TO_NUMBER} is not a valid phone number."))

        with self.assertLogs("util.sms", level="DEBUG") as logs:
            result = sms.send_sms(to_number=TO_NUMBER, from_number=FROM_NUMBER, body=SECRET_BODY)

        self.assertFalse(result)
        output = "\n".join(record.getMessage() for record in logs.records)
        self.assertNotIn(TO_NUMBER, output)
        self.assertIn("The 'To' number ***2671 is not a valid phone number.", output)


@tag("batch_sms")
class MaskPhoneTests(SimpleTestCase):
    def test_keeps_only_the_last_four_digits(self):
        self.assertEqual(sms._mask_phone("+1 (415) 555-2671"), "***2671")

    def test_short_or_missing_numbers_reveal_nothing(self):
        self.assertEqual(sms._mask_phone("1234"), "***")
        self.assertEqual(sms._mask_phone(None), "***")


@tag("batch_sms")
class SmsPlaintextConversionLogPrivacyTests(SimpleTestCase):
    def _convert(self, body):
        with self.assertLogs("api.agent.comms.outbound_delivery", level="DEBUG") as logs:
            result = _convert_sms_body_to_plaintext(body)
        return result, logs.records

    def test_html_body_stays_out_of_info_logs(self):
        result, records = self._convert(f"<p>{SECRET_BODY}</p>")
        self.assertIn("cholesterol", result)
        self.assertNotIn("cholesterol", _info_output(records))
        # Still available when someone turns on DEBUG to investigate a conversion.
        self.assertIn("cholesterol", "\n".join(r.getMessage() for r in records))

    def test_markdown_body_stays_out_of_info_logs(self):
        result, records = self._convert(f"**Update:** {SECRET_BODY}")
        self.assertIn("cholesterol", result)
        self.assertNotIn("cholesterol", _info_output(records))

    def test_plaintext_body_stays_out_of_info_logs(self):
        result, records = self._convert(SECRET_BODY)
        self.assertEqual(result, SECRET_BODY)
        self.assertNotIn("cholesterol", _info_output(records))
