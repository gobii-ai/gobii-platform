"""Custom allauth adapter hooks."""

import logging
from typing import Iterable

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.core.exceptions import ValidationError


logger = logging.getLogger(__name__)


class GobiiAccountAdapter(DefaultAccountAdapter):
    """Reject signups that use a blocked email domain."""

    def clean_email(self, email: str) -> str:
        cleaned_email = super().clean_email(email)
        domain = cleaned_email.rsplit("@", 1)[-1].lower()

        blocked_domain = self._match_blocked_domain(
            domain, getattr(settings, "SIGNUP_BLOCKED_EMAIL_DOMAINS", ())
        )
        if blocked_domain:
            logger.warning(
                "Signup rejected for blocked email domain",
                extra={"domain": blocked_domain},
            )
            raise ValidationError(
                f"We can't create accounts with email addresses from {blocked_domain}. "
                "Please use a different email."
            )

        return cleaned_email

    @staticmethod
    def _match_blocked_domain(domain: str, blocked_domains: Iterable[str] | None) -> str | None:
        if not blocked_domains:
            return None

        for blocked in blocked_domains:
            if domain == blocked or domain.endswith(f".{blocked}"):
                return blocked

        return None

