"""Custom allauth SignupForm that injects a Cloudflare Turnstile field.

Placed at top-level so it can be imported via the dotted path
``ACCOUNT_FORMS = {"signup": "turnstile_signup.SignupFormWithTurnstile"}``
"""

from allauth.account.forms import SignupForm
from turnstile.fields import TurnstileField
from allauth.account.forms import LoginForm


class SignupFormWithTurnstile(SignupForm):
    """Require a successful Turnstile validation to complete signup."""

    turnstile = TurnstileField()

    # Nothing else is needed—the field's own ``validate`` method performs the
    # server-side verification during ``form.is_valid()``.  Once validation
    # passes, we simply fall back to the original allauth behaviour.

    # Note: If you later add extra custom fields, remember to call
    # ``super().save(request)`` as usual. 


class LoginFormWithTurnstile(LoginForm):
    """Require a successful Turnstile validation to log in."""

    turnstile = TurnstileField()

    # Validation handled by field; credentials check runs afterwards. 