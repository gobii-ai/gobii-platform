from allauth.account.adapter import get_adapter
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings, tag

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
