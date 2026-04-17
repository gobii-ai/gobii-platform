"""Custom allauth SignupForm that injects a Cloudflare Turnstile field.

Placed at top-level so it can be imported via the dotted path
``ACCOUNT_FORMS = {"signup": "turnstile_signup.SignupFormWithTurnstile"}``
"""

from allauth.account.forms import SignupForm
from turnstile.fields import TurnstileField
from allauth.account.forms import LoginForm
from turnstile.widgets import TurnstileWidget
from django.forms.utils import flatatt
from django.utils.html import format_html


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
