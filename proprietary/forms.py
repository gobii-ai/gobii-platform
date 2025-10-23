"""Forms for proprietary views."""

from django import forms
from django.conf import settings


class SupportForm(forms.Form):
    """Support request form with optional Cloudflare Turnstile validation."""

    name = forms.CharField(max_length=100)
    email = forms.EmailField(max_length=254)
    subject = forms.CharField(max_length=200)
    message = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if settings.TURNSTILE_ENABLED:
            # Import lazily so the turnstile package is only required when enabled.
            from turnstile.fields import TurnstileField  # type: ignore[import]

            self.fields["turnstile"] = TurnstileField(label="")
