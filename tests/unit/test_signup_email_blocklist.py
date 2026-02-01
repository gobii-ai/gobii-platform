from allauth.account.adapter import get_adapter
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.test import SimpleTestCase, TestCase, override_settings, tag

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


@tag("batch_email_blocklist")
class SignupRegistrationGateTests(TestCase):
    @override_settings(ACCOUNT_ALLOW_REGISTRATION=False)
    def test_signup_disabled(self) -> None:
        adapter = get_adapter()

        self.assertFalse(adapter.is_open_for_signup(HttpRequest()))

    @override_settings(ACCOUNT_ALLOW_REGISTRATION=True)
    def test_signup_enabled(self) -> None:
        adapter = get_adapter()

        self.assertTrue(adapter.is_open_for_signup(HttpRequest()))
