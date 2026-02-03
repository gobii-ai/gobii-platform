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
class SignupPasswordGateTests(TestCase):
    @override_settings(ACCOUNT_ALLOW_PASSWORD_SIGNUP=False, ACCOUNT_ALLOW_SOCIAL_SIGNUP=False)
    def test_signup_disabled_when_all_signup_closed(self) -> None:
        adapter = get_adapter()
        request = HttpRequest()
        request.method = "GET"

        self.assertFalse(adapter.is_open_for_signup(request))

    @override_settings(ACCOUNT_ALLOW_PASSWORD_SIGNUP=False, ACCOUNT_ALLOW_SOCIAL_SIGNUP=True)
    def test_signup_page_open_for_social_only(self) -> None:
        adapter = get_adapter()
        request = HttpRequest()
        request.method = "GET"

        self.assertTrue(adapter.is_open_for_signup(request))

    @override_settings(ACCOUNT_ALLOW_PASSWORD_SIGNUP=False, ACCOUNT_ALLOW_SOCIAL_SIGNUP=True)
    def test_password_signup_blocked_when_disabled(self) -> None:
        adapter = get_adapter()
        request = HttpRequest()
        request.method = "POST"

        self.assertFalse(adapter.is_open_for_signup(request))

    @override_settings(ACCOUNT_ALLOW_PASSWORD_SIGNUP=True)
    def test_password_signup_enabled(self) -> None:
        adapter = get_adapter()
        request = HttpRequest()
        request.method = "POST"

        self.assertTrue(adapter.is_open_for_signup(request))
