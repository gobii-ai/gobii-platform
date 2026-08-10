from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse


@tag("batch_pages")
@override_settings(TURNSTILE_ENABLED=False, ACCOUNT_FORMS={})
class AccountLoginSessionTests(TestCase):
    def setUp(self):
        self.password = "password123"
        self.user = get_user_model().objects.create_user(
            username="persistent-login@example.com",
            email="persistent-login@example.com",
            password=self.password,
        )

    def test_login_page_does_not_offer_remember_checkbox(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="remember"')

    def test_password_login_persists_without_remember_field(self):
        expected_expiry_age = 14 * 24 * 60 * 60
        self.assertEqual(settings.SESSION_COOKIE_AGE, expected_expiry_age)

        response = self.client.post(
            reverse("account_login"),
            {
                "login": self.user.email,
                "password": self.password,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertGreaterEqual(
            self.client.session.get_expiry_age(),
            expected_expiry_age - 5,
        )
        session_cookie = response.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(
            int(session_cookie["max-age"]),
            expected_expiry_age,
        )
