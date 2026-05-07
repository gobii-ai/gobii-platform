"""Custom allauth SignupForm that injects a Cloudflare Turnstile field.

Placed at top-level so it can be imported via the dotted path
``ACCOUNT_FORMS = {"signup": "turnstile_signup.SignupFormWithTurnstile"}``
"""

import logging

from allauth.account.adapter import get_adapter
from allauth.account.forms import LoginForm
from allauth.account.forms import SignupForm
from django.forms.utils import flatatt
from django.utils.html import format_html
from turnstile.fields import TurnstileField
from turnstile.widgets import TurnstileWidget


logger = logging.getLogger(__name__)


def _bool_label(value):
    return "true" if value else "false"


def _redact_login(raw_login):
    login = (raw_login or "").strip()
    if not login:
        return ""
    local_part, separator, domain = login.partition("@")
    if separator and domain:
        prefix = local_part[:1] if local_part else "u"
        return f"{prefix}***@{domain.lower()}"
    return f"{login[:1]}***"


def _truncate_for_log(value, max_length=180):
    normalized = " ".join((value or "").split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length - 3]}..."


def _sanitize_log_token(value, max_length=120):
    normalized = "_".join((value or "").split())
    sanitized = "".join(
        char if char.isalnum() or char in "@._+-*" else "_"
        for char in normalized
    )
    if len(sanitized) <= max_length:
        return sanitized
    return f"{sanitized[:max_length - 3]}..."


class AuthTurnstileWidget(TurnstileWidget):
    def render(self, name, value, attrs=None, renderer=None):
        if self.is_hidden:
            return ""
        final_attrs = self.build_attrs(self.attrs, attrs)
        return format_html('<div class="cf-turnstile"{}></div>', flatatt(final_attrs))


class AuthTurnstileField(TurnstileField):
    widget = AuthTurnstileWidget


class SignupFormWithTurnstile(SignupForm):
    """Require a successful Turnstile validation to complete signup."""

    turnstile = AuthTurnstileField()

    # Nothing else is needed—the field's own ``validate`` method performs the
    # server-side verification during ``form.is_valid()``.  Once validation
    # passes, we simply fall back to the original allauth behaviour.

    # Note: If you later add extra custom fields, remember to call
    # ``super().save(request)`` as usual. 


class LoginFormWithTurnstile(LoginForm):
    """Require a successful Turnstile validation to log in."""

    turnstile = AuthTurnstileField(
        callback="gobiiLoginTurnstileSuccess",
        **{
            "expired-callback": "gobiiLoginTurnstileExpired",
            "timeout-callback": "gobiiLoginTurnstileExpired",
            "error-callback": "gobiiLoginTurnstileError",
        },
    )

    # Validation handled by field; credentials check runs afterwards.
    def is_valid(self):
        is_valid = super().is_valid()
        if not is_valid:
            self._log_invalid_login_post()
        return is_valid

    def _log_invalid_login_post(self):
        if getattr(self, "_gobii_invalid_login_logged", False):
            return
        self._gobii_invalid_login_logged = True

        request = self.request
        if not request or request.method != "POST":
            return

        login = self.data.get("login", "")
        error_fields = sorted(str(field) for field in self.errors.keys())
        error_fields_label = ",".join(error_fields)
        password_present = bool(self.data.get("password"))
        turnstile_token_present = bool(
            (self.data.get("cf-turnstile-response") or "").strip()
        )
        ajax = get_adapter(request).is_ajax(request)
        user_agent = _truncate_for_log(request.META.get("HTTP_USER_AGENT", ""))
        redacted_login = _sanitize_log_token(_redact_login(login))
        path = request.path

        log_fields = {
            "path": path,
            "login": redacted_login,
            "error_fields": error_fields_label,
            "password_present": password_present,
            "turnstile_token_present": turnstile_token_present,
            "ajax": ajax,
            "user_agent": user_agent,
        }
        logger.warning(
            "Invalid login POST path=%s login=%s error_fields=%s password_present=%s "
            "turnstile_token_present=%s ajax=%s user_agent=%s",
            path,
            redacted_login,
            error_fields_label,
            _bool_label(password_present),
            _bool_label(turnstile_token_present),
            _bool_label(ajax),
            user_agent,
            extra=log_fields,
        )
