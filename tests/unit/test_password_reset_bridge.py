import re

from allauth.account.views import INTERNAL_RESET_SESSION_KEY
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from config.account_views import (
    PASSWORD_RESET_BRIDGE_INVALID_SENTINEL,
    PASSWORD_RESET_BRIDGE_SESSION_KEY,
)


User = get_user_model()


@tag("batch_email")
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetBridgeTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="password-reset-user",
            email="password-reset@example.com",
            password="pw123456",
        )

    def _request_reset(self) -> str:
        response = self.client.post(
            reverse("account_reset_password"),
            {"email": self.user.email},
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("account_reset_password_done"))
        self.assertEqual(len(mail.outbox), 1)

        body = mail.outbox[0].body
        self.assertNotIn("/accounts/password/reset/key/", body)
        match = re.search(
            r"https?://[^\s]+/accounts/password/reset/link/(?P<key>[^\s/]+)/",
            body,
        )
        self.assertIsNotNone(match, body)
        return match.group("key")

    def test_password_reset_email_uses_bridge_link(self) -> None:
        opaque_key = self._request_reset()

        self.assertIn("-", opaque_key)

    def test_bridge_start_stores_bridge_session_without_touching_allauth_session(self) -> None:
        opaque_key = self._request_reset()

        response = self.client.get(
            reverse("account_reset_password_bridge_start", kwargs={"key": opaque_key})
        )

        self.assertRedirects(response, reverse("account_reset_password_bridge_confirm"))
        session = self.client.session
        self.assertEqual(session[PASSWORD_RESET_BRIDGE_SESSION_KEY], opaque_key)
        self.assertNotIn(INTERNAL_RESET_SESSION_KEY, session)

    def test_confirm_page_renders_continue_post_without_exposing_token(self) -> None:
        opaque_key = self._request_reset()
        self.client.get(
            reverse("account_reset_password_bridge_start", kwargs={"key": opaque_key})
        )

        response = self.client.get(reverse("account_reset_password_bridge_confirm"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'action="{reverse("account_reset_password_bridge_continue")}"',
            html=False,
        )
        self.assertContains(response, "Continue to Reset Password")
        self.assertNotContains(response, opaque_key)
        self.assertNotContains(response, "/accounts/password/reset/key/")

    def test_continue_redirects_into_existing_allauth_flow_and_clears_bridge_state(self) -> None:
        opaque_key = self._request_reset()
        uidb36, _, token = opaque_key.partition("-")
        self.client.get(
            reverse("account_reset_password_bridge_start", kwargs={"key": opaque_key})
        )

        response = self.client.post(
            reverse("account_reset_password_bridge_continue"),
            follow=True,
        )

        self.assertEqual(
            response.redirect_chain[0][0],
            reverse(
                "account_reset_password_from_key",
                kwargs={"uidb36": uidb36, "key": token},
            ),
        )
        self.assertEqual(
            response.redirect_chain[1][0],
            reverse(
                "account_reset_password_from_key",
                kwargs={"uidb36": uidb36, "key": "set-password"},
            ),
        )
        self.assertContains(response, "Set New Password")
        self.assertNotIn(PASSWORD_RESET_BRIDGE_SESSION_KEY, self.client.session)

    def test_malformed_bridge_key_never_reaches_token_url(self) -> None:
        response = self.client.get(
            reverse("account_reset_password_bridge_start", kwargs={"key": "malformed"}),
            follow=True,
        )

        self.assertEqual(
            response.request["PATH_INFO"],
            reverse("account_reset_password_bridge_confirm"),
        )
        self.assertContains(response, "request a <a")
        self.assertNotContains(response, "malformed")
        self.assertEqual(
            self.client.session[PASSWORD_RESET_BRIDGE_SESSION_KEY],
            PASSWORD_RESET_BRIDGE_INVALID_SENTINEL,
        )

    def test_confirm_page_without_bridge_state_shows_reset_fallback(self) -> None:
        response = self.client.get(reverse("account_reset_password_bridge_confirm"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This password reset link is no longer available.")
        self.assertNotContains(response, "Continue to Reset Password")

    def test_continue_without_bridge_state_redirects_to_request_reset(self) -> None:
        response = self.client.post(reverse("account_reset_password_bridge_continue"))

        self.assertRedirects(response, reverse("account_reset_password"))
