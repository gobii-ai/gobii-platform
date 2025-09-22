from allauth.account.adapter import get_adapter
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings, tag

@tag("batch_email_blocklist")
class SignupEmailBlocklistTests(SimpleTestCase):
    @override_settings(SIGNUP_BLOCKED_EMAIL_DOMAINS=["mailslurp.biz"])
    def test_blocks_exact_domain(self) -> None:
        adapter = get_adapter()

        with self.assertRaises(ValidationError) as exc:
            adapter.clean_email("user@mailslurp.biz")

        self.assertIn("mailslurp.biz", exc.exception.messages[0])

    @override_settings(SIGNUP_BLOCKED_EMAIL_DOMAINS=["mailslurp.biz"])
    def test_blocks_subdomain(self) -> None:
        adapter = get_adapter()

        with self.assertRaises(ValidationError):
            adapter.clean_email("user@inbox.mailslurp.biz")

    @override_settings(SIGNUP_BLOCKED_EMAIL_DOMAINS=["mailslurp.biz"])
    def test_allows_other_domains(self) -> None:
        adapter = get_adapter()

        cleaned = adapter.clean_email("user@example.com")

        self.assertEqual(cleaned, "user@example.com")

