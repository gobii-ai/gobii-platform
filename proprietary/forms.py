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


class PrequalifyForm(forms.Form):
    """Pre-qualification form with optional Cloudflare Turnstile validation."""

    TEAM_SIZE_CHOICES = (
        ("", "Select team size"),
        ("1-5", "1-5"),
        ("6-20", "6-20"),
        ("21-50", "21-50"),
        ("51-200", "51-200"),
        ("201-500", "201-500"),
        ("500+", "500+"),
    )
    VOLUME_CHOICES = (
        ("", "Select monthly volume"),
        ("under_250", "Under 250 tasks"),
        ("250_1000", "250-1,000 tasks"),
        ("1000_5000", "1,000-5,000 tasks"),
        ("5000_plus", "5,000+ tasks"),
    )
    BUDGET_CHOICES = (
        ("", "Select budget range"),
        ("under_500", "Under $500 / month"),
        ("500_2000", "$500 - $2,000 / month"),
        ("2000_10000", "$2,000 - $10,000 / month"),
        ("10000_plus", "$10,000+ / month"),
    )
    TIMELINE_CHOICES = (
        ("", "Select timeline"),
        ("asap", "Immediately"),
        ("this_quarter", "This quarter"),
        ("next_quarter", "Next quarter"),
        ("exploring", "Exploring"),
    )

    name = forms.CharField(max_length=100, label="Full name")
    email = forms.EmailField(max_length=254, label="Work email")
    company = forms.CharField(max_length=200, label="Company")
    role = forms.CharField(max_length=120, label="Role or title")
    team_size = forms.ChoiceField(choices=TEAM_SIZE_CHOICES, label="Team size")
    monthly_volume = forms.ChoiceField(choices=VOLUME_CHOICES, label="Monthly automation volume")
    budget_range = forms.ChoiceField(choices=BUDGET_CHOICES, label="Budget range")
    timeline = forms.ChoiceField(choices=TIMELINE_CHOICES, label="Timeline")
    use_case = forms.CharField(widget=forms.Textarea, label="Primary use case")
    website = forms.CharField(required=False, label="Website or LinkedIn")
    notes = forms.CharField(required=False, widget=forms.Textarea, label="Additional context")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if settings.TURNSTILE_ENABLED:
            # Import lazily so the turnstile package is only required when enabled.
            from turnstile.fields import TurnstileField  # type: ignore[import]

            self.fields["turnstile"] = TurnstileField(label="")
