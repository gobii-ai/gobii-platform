from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils.translation import ngettext


class MaximumLengthPasswordValidator:
    def __init__(self, max_length=None):
        self.max_length = max_length

    def validate(self, password, user=None):
        max_length = self._get_max_length()
        if len(password) <= max_length:
            return

        raise ValidationError(
            ngettext(
                "This password is too long. It must contain at most %(max_length)d character.",
                "This password is too long. It must contain at most %(max_length)d characters.",
                max_length,
            ),
            code="password_too_long",
            params={"max_length": max_length},
        )

    def get_help_text(self):
        max_length = self._get_max_length()
        return ngettext(
            "Your password must contain at most %(max_length)d character.",
            "Your password must contain at most %(max_length)d characters.",
            max_length,
        ) % {"max_length": max_length}

    def _get_max_length(self):
        max_length = (
            settings.ACCOUNT_PASSWORD_MAX_LENGTH
            if self.max_length is None
            else self.max_length
        )
        if (
            isinstance(max_length, bool)
            or not isinstance(max_length, int)
            or max_length < 1
        ):
            raise ImproperlyConfigured(
                "ACCOUNT_PASSWORD_MAX_LENGTH must be a positive integer."
            )
        return max_length
