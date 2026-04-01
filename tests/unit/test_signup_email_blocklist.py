from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from allauth.core import context
from allauth.account.adapter import get_adapter
from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.utils import get_login_redirect_url
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings, tag
from django.urls import reverse
from waffle.testutils import override_flag

from config.allauth_adapter import GobiiAccountAdapter
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
)
from util.urls import IMMERSIVE_APP_BASE_PATH

@tag("batch_email_blocklist")
class SignupEmailBlocklistTests(SimpleTestCase):
    @override_settings(
        GOBII_EMAIL_DOMAIN_ALLOWLIST=set(),
        GOBII_EMAIL_DOMAIN_BLOCKLIST=set(),
        GOBII_EMAIL_BLOCK_DISPOSABLE=True,
    )
    @patch("config.allauth_adapter.is_disposable_domain", return_value=True)
    def test_blocks_disposable_domain(self, is_disposable_mock) -> None:
        adapter = get_adapter()

        with self.assertRaises(ValidationError) as exc:
            adapter.clean_email("user@disposable.test")

        self.assertEqual(
            exc.exception.messages[0],
            "We are unable to create an account with this email address. Please use a different one.",
        )
        is_disposable_mock.assert_called_once_with("disposable.test")

    @override_settings(
        GOBII_EMAIL_DOMAIN_ALLOWLIST={"mailslurp.biz"},
        GOBII_EMAIL_DOMAIN_BLOCKLIST={"mailslurp.biz"},
        GOBII_EMAIL_BLOCK_DISPOSABLE=True,
    )
    @patch("config.allauth_adapter.is_disposable_domain", return_value=True)
    def test_allowlist_overrides_disposable_detection(self, is_disposable_mock) -> None:
        adapter = get_adapter()

        cleaned = adapter.clean_email("user@mailslurp.biz")

        self.assertEqual(cleaned, "user@mailslurp.biz")
        is_disposable_mock.assert_not_called()

    @override_settings(
        GOBII_EMAIL_DOMAIN_ALLOWLIST=set(),
        GOBII_EMAIL_DOMAIN_BLOCKLIST={"mailslurp.biz"},
        GOBII_EMAIL_BLOCK_DISPOSABLE=True,
    )
    @patch("config.allauth_adapter.is_disposable_domain", return_value=False)
    def test_blocklist_blocks_non_disposable_domain(self, is_disposable_mock) -> None:
        adapter = get_adapter()

        with self.assertRaises(ValidationError) as exc:
            adapter.clean_email("user@mailslurp.biz")

        self.assertEqual(
            exc.exception.messages[0],
            "We are unable to create an account with this email address. Please use a different one.",
        )
        is_disposable_mock.assert_not_called()

    @override_settings(
        GOBII_EMAIL_DOMAIN_ALLOWLIST=set(),
        GOBII_EMAIL_DOMAIN_BLOCKLIST={"mailslurp.biz"},
        GOBII_EMAIL_BLOCK_DISPOSABLE=True,
    )
    @patch("config.allauth_adapter.is_disposable_domain", return_value=False)
    @patch("config.allauth_adapter.logger.warning")
    def test_blocklist_logs_reason_domain_and_redacted_email(
        self,
        warning_mock,
        is_disposable_mock,
    ) -> None:
        adapter = get_adapter()

        with self.assertRaises(ValidationError):
            adapter.clean_email("user@mailslurp.biz")

        warning_mock.assert_called_once()
        _, kwargs = warning_mock.call_args
        self.assertEqual(kwargs["extra"]["reason"], "blocklist")
        self.assertEqual(kwargs["extra"]["domain"], "mailslurp.biz")
        self.assertEqual(kwargs["extra"]["email"], "u***@mailslurp.biz")
        is_disposable_mock.assert_not_called()

    @override_settings(
        GOBII_EMAIL_DOMAIN_ALLOWLIST=set(),
        GOBII_EMAIL_DOMAIN_BLOCKLIST=set(),
        SIGNUP_BLOCKED_EMAIL_DOMAINS=["legacy-block.test"],
        GOBII_EMAIL_BLOCK_DISPOSABLE=True,
    )
    @patch("config.allauth_adapter.is_disposable_domain", return_value=False)
    def test_legacy_signup_blocked_domains_setting_still_blocks(self, is_disposable_mock) -> None:
        adapter = get_adapter()

        with self.assertRaises(ValidationError) as exc:
            adapter.clean_email("user@legacy-block.test")

        self.assertEqual(
            exc.exception.messages[0],
            "We are unable to create an account with this email address. Please use a different one.",
        )
        is_disposable_mock.assert_not_called()

    @override_settings(
        GOBII_EMAIL_DOMAIN_ALLOWLIST=set(),
        GOBII_EMAIL_DOMAIN_BLOCKLIST=set(),
        GOBII_EMAIL_BLOCK_DISPOSABLE=True,
    )
    @patch("config.allauth_adapter.is_disposable_domain", return_value=False)
    def test_allows_normal_domain(self, is_disposable_mock) -> None:
        adapter = get_adapter()

        cleaned = adapter.clean_email("user@example.com")

        self.assertEqual(cleaned, "user@example.com")
        is_disposable_mock.assert_called_once_with("example.com")

    @override_settings(
        GOBII_EMAIL_DOMAIN_ALLOWLIST=set(),
        GOBII_EMAIL_DOMAIN_BLOCKLIST={"mailslurp.biz"},
        GOBII_EMAIL_BLOCK_DISPOSABLE=True,
    )
    @patch("config.allauth_adapter.is_disposable_domain", return_value=False)
    @patch.object(
        DefaultAccountAdapter,
        "clean_email",
        side_effect=ValidationError(
            "We are unable to create an account with this email address. Please use a different one."
        ),
    )
    def test_blocklist_uses_custom_message_even_if_super_raises_generic(
        self,
        _super_clean_email_mock,
        is_disposable_mock,
    ) -> None:
        adapter = get_adapter()

        with self.assertRaises(ValidationError) as exc:
            adapter.clean_email("user@mailslurp.biz")

        self.assertEqual(
            exc.exception.messages[0],
            "We are unable to create an account with this email address. Please use a different one.",
        )
        is_disposable_mock.assert_not_called()


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


@tag("batch_email_blocklist")
class TrialOnboardingAdapterTests(TestCase):
    def _build_request(self) -> HttpRequest:
        request = HttpRequest()
        request.method = "POST"
        request.user = AnonymousUser()
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        return request

    @tag("batch_email_blocklist")
    def test_pre_login_marks_plan_selection_required_for_signup(self) -> None:
        adapter = GobiiAccountAdapter()
        request = self._build_request()
        request.session[TRIAL_ONBOARDING_PENDING_SESSION_KEY] = True
        request.session[TRIAL_ONBOARDING_TARGET_SESSION_KEY] = TRIAL_ONBOARDING_TARGET_AGENT_UI
        request.session.save()

        user = get_user_model().objects.create_user(
            email="signup-flow@test.com",
            username="signup_flow_user",
            password="pw",
        )
        adapter.pre_login(
            request,
            user,
            email_verification=None,
            signal_kwargs={},
            email=user.email,
            signup=True,
            redirect_url=None,
        )

        self.assertTrue(request.session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY))

    @tag("batch_email_blocklist")
    def test_pre_login_keeps_plan_selection_false_for_existing_login(self) -> None:
        adapter = GobiiAccountAdapter()
        request = self._build_request()
        request.session[TRIAL_ONBOARDING_PENDING_SESSION_KEY] = True
        request.session[TRIAL_ONBOARDING_TARGET_SESSION_KEY] = TRIAL_ONBOARDING_TARGET_AGENT_UI
        request.session[TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY] = False
        request.session.save()

        user = get_user_model().objects.create_user(
            email="login-flow@test.com",
            username="login_flow_user",
            password="pw",
        )
        adapter.pre_login(
            request,
            user,
            email_verification=None,
            signal_kwargs={},
            email=user.email,
            signup=False,
            redirect_url=None,
        )

        self.assertFalse(request.session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY))

    @tag("batch_email_blocklist")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("util.personal_signup_preview.can_user_use_personal_agents_and_api", return_value=False)
    def test_signup_redirect_uses_agent_preview_when_enabled_without_next(
        self,
        _mock_can_user_use_personal_agents_and_api,
    ) -> None:
        user = get_user_model().objects.create_user(
            email="preview-signup@test.com",
            username="preview_signup_user",
            password="pw",
        )
        request = RequestFactory().get(reverse("account_signup"))
        request.user = user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        with override_flag("personal_agent_signup_starter_charter", active=True):
            redirect_url = get_login_redirect_url(request, signup=True)

        parsed = urlparse(redirect_url)
        self.assertEqual(parsed.path, f"{IMMERSIVE_APP_BASE_PATH}/agents/new")
        self.assertEqual(parse_qs(parsed.query).get("spawn"), ["1"])

    @tag("batch_email_blocklist")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("util.personal_signup_preview.can_user_use_personal_agents_and_api", return_value=False)
    def test_signup_redirect_keeps_next_param_over_preview_fallback(
        self,
        _mock_can_user_use_personal_agents_and_api,
    ) -> None:
        user = get_user_model().objects.create_user(
            email="preview-signup-next@test.com",
            username="preview_signup_next_user",
            password="pw",
        )
        next_url = reverse("billing")
        request = RequestFactory().get(reverse("account_signup"), {"next": next_url})
        request.user = user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        with override_flag("personal_agent_signup_starter_charter", active=True):
            context.request = request
            self.addCleanup(lambda: setattr(context, "request", None))
            redirect_url = get_login_redirect_url(request, signup=True)

        self.assertEqual(redirect_url, next_url)
