# admin_forms.py  (optional file)
from django import forms
import phonenumbers
from django.utils import timezone
from decimal import Decimal
from constants.plans import PlanNamesChoices
from constants.grant_types import GrantTypeChoices

class TestSmsForm(forms.Form):
    to      = forms.CharField(label="Destination number")
    body    = forms.CharField(label="Message", widget=forms.Textarea, initial="Test 🚀")

    def clean_to(self):
        raw = self.cleaned_data["to"]
        try:
            parsed = phonenumbers.parse(raw, "US")             # or None for strict intl
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            raise forms.ValidationError("Not a valid phone number.")


class GrantPlanCreditsForm(forms.Form):
    plan = forms.ChoiceField(
        label="Plan",
        choices=PlanNamesChoices.choices,
        help_text="Grant credits to all users currently on this plan.",
    )
    credits = forms.DecimalField(
        label="Credits",
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        help_text="Number of credits to grant per user (supports fractional)",
    )
    grant_type = forms.ChoiceField(
        label="Grant Type",
        choices=GrantTypeChoices.choices,
        initial=GrantTypeChoices.PROMO,
        help_text="Type of grant; defaults to PROMO",
    )
    grant_date = forms.DateTimeField(
        label="Grant Date",
        initial=timezone.now,
        help_text="When the credits are considered granted",
    )
    expiration_date = forms.DateTimeField(
        label="Expiration Date",
        help_text="When the credits expire",
    )
    dry_run = forms.BooleanField(
        label="Dry Run",
        required=False,
        initial=False,
        help_text="If checked, shows how many users would be granted without creating TaskCredits",
    )
