from allauth.account.adapter import get_adapter
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings, tag
from django.urls import reverse

from config.password_validation import MaximumLengthPasswordValidator


@tag("batch_system_settings")
class AccountPasswordMaxLengthTests(SimpleTestCase):
    def test_default_password_max_length_is_128(self):
        self.assertEqual(settings.ACCOUNT_PASSWORD_MAX_LENGTH, 128)

    @override_settings(ACCOUNT_PASSWORD_MAX_LENGTH=8)
    def test_validator_rejects_passwords_longer_than_configured_limit(self):
        validator = MaximumLengthPasswordValidator()

        with self.assertRaises(ValidationError) as exc:
            validator.validate("x" * 9)

        self.assertEqual(exc.exception.code, "password_too_long")
        self.assertEqual(
            exc.exception.messages,
            ["This password is too long. It must contain at most 8 characters."],
        )

    @override_settings(ACCOUNT_PASSWORD_MAX_LENGTH=8)
    def test_allauth_password_cleaning_uses_configured_limit(self):
        password = "aB3!zzzz"

        self.assertEqual(get_adapter().clean_password(password), password)

        with self.assertRaises(ValidationError) as exc:
            get_adapter().clean_password(password + "x")

        self.assertEqual(
            exc.exception.messages,
            ["This password is too long. It must contain at most 8 characters."],
        )


@tag("batch_system_settings")
class AccountPasswordMaxLengthTemplateTests(TestCase):
    @override_settings(ACCOUNT_PASSWORD_MAX_LENGTH=12)
    def test_signup_new_password_fields_include_configured_maxlength(self):
        response = self.client.get(reverse("account_signup"))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        for field_name in ("password1", "password2"):
            password_input = soup.select_one(f'input[name="{field_name}"]')
            self.assertIsNotNone(password_input)
            self.assertEqual(password_input.get("maxlength"), "12")

    @override_settings(ACCOUNT_PASSWORD_MAX_LENGTH=12)
    def test_login_password_field_does_not_include_maxlength(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        password_input = soup.select_one('input[name="password"]')
        self.assertIsNotNone(password_input)
        self.assertIsNone(password_input.get("maxlength"))
