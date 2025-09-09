# admin_forms.py  (optional file)
from django import forms
from django.forms import ModelForm
from .models import CommsChannel, AgentEmailAccount
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

class AgentEmailAccountForm(ModelForm):
    """Admin form for AgentEmailAccount with plaintext password inputs.

    - Provides `smtp_password` and `imap_password` as write-only fields.
    - On save, encrypts and stores into *_password_encrypted fields.
    - Validates basic requirements when enabling outbound/inbound.
    """

    smtp_password = forms.CharField(
        label="SMTP Password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing password.",
    )

    imap_password = forms.CharField(
        label="IMAP Password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing password.",
    )

    class Meta:
        model = AgentEmailAccount
        fields = [
            # Endpoint
            "endpoint",
            # SMTP
            "smtp_host",
            "smtp_port",
            "smtp_security",
            "smtp_auth",
            "smtp_username",
            # passwords handled via form-only fields
            "is_outbound_enabled",
            # IMAP (Phase 2 storage only)
            "imap_host",
            "imap_port",
            "imap_security",
            "imap_username",
            "imap_folder",
            "is_inbound_enabled",
            "imap_idle_enabled",
            "poll_interval_sec",
        ]

    def clean(self):
        cleaned = super().clean()
        instance = self.instance
        # Basic gate: endpoint must be email + agent-owned
        ep = cleaned.get("endpoint") or getattr(instance, "endpoint", None)
        if ep is not None:
            if ep.channel != CommsChannel.EMAIL:
                raise forms.ValidationError("AgentEmailAccount must be attached to an email endpoint.")
            if ep.owner_agent_id is None:
                raise forms.ValidationError("AgentEmailAccount may only be attached to agent-owned endpoints.")

        # Outbound requirements if enabling
        if cleaned.get("is_outbound_enabled"):
            for field in ["smtp_host", "smtp_port", "smtp_security", "smtp_auth"]:
                if not cleaned.get(field):
                    self.add_error(field, "Required when outbound is enabled")
            if cleaned.get("smtp_auth") and cleaned.get("smtp_auth") != "none":
                if not cleaned.get("smtp_username"):
                    self.add_error("smtp_username", "Username required for authenticated SMTP")
                # password can be set previously; require either new input or existing
                if not cleaned.get("smtp_password") and not getattr(instance, "smtp_password_encrypted", None):
                    self.add_error("smtp_password", "Password required for authenticated SMTP")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        smtp_password = self.cleaned_data.get("smtp_password")
        imap_password = self.cleaned_data.get("imap_password")
        is_new = obj.pk is None
        if smtp_password:
            from .encryption import SecretsEncryption
            obj.smtp_password_encrypted = SecretsEncryption.encrypt_value(smtp_password)
        if imap_password:
            from .encryption import SecretsEncryption
            obj.imap_password_encrypted = SecretsEncryption.encrypt_value(imap_password)
        if commit:
            obj.save()
            try:
                # Track create vs update for analytics (best-effort)
                user_id = getattr(getattr(obj.endpoint.owner_agent, 'user', None), 'id', None)
                if user_id:
                    Analytics.track_event(
                        user_id=user_id,
                        event=AnalyticsEvent.EMAIL_ACCOUNT_CREATED if is_new else AnalyticsEvent.EMAIL_ACCOUNT_UPDATED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'endpoint': obj.endpoint.address,
                            'agent_id': str(getattr(obj.endpoint.owner_agent, 'id', '')),
                        },
                    )
            except Exception:
                pass
        return obj
import phonenumbers
from django.utils import timezone
from decimal import Decimal
from constants.plans import PlanNamesChoices
from constants.grant_types import GrantTypeChoices
from django.contrib.admin.widgets import AdminSplitDateTime

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
    grant_date = forms.SplitDateTimeField(
        label="Grant Date",
        initial=timezone.now,
        help_text="When the credits are considered granted",
        widget=AdminSplitDateTime,
    )
    expiration_date = forms.SplitDateTimeField(
        label="Expiration Date",
        help_text="When the credits expire",
        widget=AdminSplitDateTime,
    )
    dry_run = forms.BooleanField(
        label="Dry Run",
        required=False,
        initial=False,
        help_text="If checked, shows how many users would be granted without creating TaskCredits",
    )
    only_if_out_of_credits = forms.BooleanField(
        label="Only if out of credits",
        required=False,
        initial=False,
        help_text="Grant only to users who currently have 0 available credits",
    )
    export_csv = forms.BooleanField(
        label="Export CSV (dry‑run)",
        required=False,
        initial=False,
        help_text="When Dry Run is checked, download a CSV of affected users",
    )


class GrantCreditsByUserIdsForm(forms.Form):
    user_ids = forms.CharField(
        label="User IDs",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Paste user IDs (integers), one per line or comma-separated"}),
        help_text="List of user IDs (integers) to grant credits to",
    )
    plan = forms.ChoiceField(
        label="Plan",
        choices=PlanNamesChoices.choices,
        help_text="Plan value to set on the TaskCredit grant",
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
    grant_date = forms.SplitDateTimeField(
        label="Grant Date",
        initial=timezone.now,
        help_text="When the credits are considered granted",
        widget=AdminSplitDateTime,
    )
    expiration_date = forms.SplitDateTimeField(
        label="Expiration Date",
        help_text="When the credits expire",
        widget=AdminSplitDateTime,
    )
    dry_run = forms.BooleanField(
        label="Dry Run",
        required=False,
        initial=False,
        help_text="If checked, shows how many users would be granted without creating TaskCredits",
    )
    only_if_out_of_credits = forms.BooleanField(
        label="Only if out of credits",
        required=False,
        initial=False,
        help_text="Grant only to users who currently have 0 available credits",
    )
    export_csv = forms.BooleanField(
        label="Export CSV (dry‑run)",
        required=False,
        initial=False,
        help_text="When Dry Run is checked, download a CSV of affected users",
    )
