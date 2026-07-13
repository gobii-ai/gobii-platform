import re
from decimal import Decimal

import phonenumbers
from django import forms
from django.contrib.admin.widgets import AdminSplitDateTime
from django.core.validators import validate_email
from django.forms import ModelForm
from django.utils import timezone

from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices
from config.stripe_fields import (
    STRIPE_CONFIG_FIELDS,
    StripeConfigFieldSpec,
    StripeValueKind,
    first_string,
)
from .models import AgentEmailAccount, CommsChannel, StripeConfig, TrialPromo, TrialPromoAllowedEmail, UserFlagDefinition
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


def _validate_single_stripe_price_id(value: str) -> None:
    if "," in value:
        raise forms.ValidationError("Enter a single Stripe price ID (no commas).")


def _stripe_config_form_field(spec: StripeConfigFieldSpec) -> forms.Field:
    options = {
        "label": spec.label,
        "required": False,
        "help_text": spec.help_text,
    }
    if spec.value_kind == StripeValueKind.NONNEGATIVE_INTEGER:
        return forms.IntegerField(min_value=0, **options)
    if spec.value_kind == StripeValueKind.SINGULAR_WITH_LEGACY_LIST:
        options["validators"] = [_validate_single_stripe_price_id]
    return forms.CharField(**options)


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
            "connection_mode",
            # IMAP (Phase 2 storage only)
            "imap_host",
            "imap_port",
            "imap_security",
            "imap_username",
            "imap_auth",
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
                if cleaned.get("smtp_auth") != "oauth2" and cleaned.get("connection_mode") != "oauth2":
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
                        properties=Analytics.with_org_properties(
                            {
                                'endpoint': obj.endpoint.address,
                                'agent_id': str(getattr(obj.endpoint.owner_agent, 'id', '')),
                            },
                            organization=getattr(getattr(obj.endpoint, 'owner_agent', None), 'organization', None),
                        ),
                    )
            except Exception:
                pass
        return obj


class TrialPromoAdminForm(forms.ModelForm):
    code = forms.CharField(
        label="Promo code",
        required=False,
        help_text="Set a new code. Leave blank when editing to keep the existing code.",
    )
    allowed_emails_bulk = forms.CharField(
        label="Add allowed emails",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": "Paste one email per line, comma-separated, or space-separated",
            }
        ),
        help_text="Appends normalized emails to this promo's allowlist. Leave blank to keep existing entries.",
    )

    class Meta:
        model = TrialPromo
        fields = (
            "name",
            "code",
            "plan",
            "trial_days",
            "payment_method_required",
            "no_payment_method_end_behavior",
            "repeat_trials_allowed",
            "email_allowlist_enabled",
            "trial_abuse_filtering_enabled",
            "trial_credit_amount",
            "max_redemptions",
            "active_from",
            "active_until",
            "is_active",
            "headline",
            "description",
            "allowed_emails_bulk",
        )

    def clean_allowed_emails_bulk(self) -> list[str]:
        raw_value = self.cleaned_data.get("allowed_emails_bulk") or ""
        normalized_emails: list[str] = []
        invalid_entries: list[str] = []

        for token in re.split(r"[\s,;]+", raw_value):
            candidate = token.strip()
            if not candidate:
                continue

            normalized = TrialPromo.normalize_allowed_email(candidate)
            try:
                validate_email(normalized)
            except forms.ValidationError:
                invalid_entries.append(candidate)
                continue

            normalized_emails.append(normalized)

        if invalid_entries:
            preview = ", ".join(invalid_entries[:5])
            suffix = "..." if len(invalid_entries) > 5 else ""
            raise forms.ValidationError(f"Invalid email address(es): {preview}{suffix}")

        return list(dict.fromkeys(normalized_emails))

    def clean_code(self) -> str:
        code = TrialPromo.normalize_code(self.cleaned_data.get("code"))
        if not code and self.instance.pk is None:
            raise forms.ValidationError("Enter a promo code.")
        if code:
            existing = TrialPromo.objects.filter(code_digest=TrialPromo.digest_code(code))
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("A trial promo with this code already exists.")
        return code

    def clean(self):
        cleaned = super().clean()
        active_from = cleaned.get("active_from")
        active_until = cleaned.get("active_until")
        if active_from and active_until and active_until <= active_from:
            raise forms.ValidationError("Active until must be after active from.")
        return cleaned

    def _append_allowed_emails(self, instance: TrialPromo) -> None:
        allowed_emails = self.cleaned_data.get("allowed_emails_bulk") or []
        if not allowed_emails:
            return

        TrialPromoAllowedEmail.objects.bulk_create(
            [
                TrialPromoAllowedEmail(
                    promo=instance,
                    normalized_email=email,
                )
                for email in allowed_emails
            ],
            ignore_conflicts=True,
        )

    def save(self, commit: bool = True):
        instance: TrialPromo = super().save(commit=False)
        code = self.cleaned_data.get("code")
        if code:
            instance.set_code(code)
        if not commit:
            save_m2m = self.save_m2m

            def save_m2m_with_allowed_emails():
                save_m2m()
                self._append_allowed_emails(instance)

            self.save_m2m = save_m2m_with_allowed_emails
            return instance

        instance.save()
        self.save_m2m()
        self._append_allowed_emails(instance)
        return instance


class GlobalAgentSkillImportForm(forms.Form):
    json_file = forms.FileField(
        label="Skill JSON file",
        help_text="Upload a JSON file exported from Global Skills admin.",
    )


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


class ReleaseSmsNumbersForm(forms.Form):
    phone_numbers = forms.CharField(
        label="SMS Numbers",
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": "Paste one E.164 phone number per line or comma-separated",
            }
        ),
        help_text="Only Twilio inventory numbers from SMS admin can be released.",
    )

    def clean_phone_numbers(self):
        raw = self.cleaned_data["phone_numbers"]
        normalized_numbers = []
        invalid_entries = []

        for token in re.split(r"[\n,]+", raw):
            candidate = token.strip()
            if not candidate:
                continue

            try:
                parsed = phonenumbers.parse(candidate, "US")
            except phonenumbers.NumberParseException:
                invalid_entries.append(candidate)
                continue

            if not phonenumbers.is_possible_number(parsed):
                invalid_entries.append(candidate)
                continue

            normalized_numbers.append(
                phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            )

        if invalid_entries:
            preview = ", ".join(invalid_entries[:5])
            suffix = "..." if len(invalid_entries) > 5 else ""
            raise forms.ValidationError(f"Invalid phone number(s): {preview}{suffix}")

        unique_numbers = list(dict.fromkeys(normalized_numbers))
        if not unique_numbers:
            raise forms.ValidationError("Provide at least one SMS number to release.")

        return unique_numbers


class FindReleaseCandidatesForm(forms.Form):
    unused_days = forms.IntegerField(
        label="Unused for at least (days)",
        min_value=1,
        initial=90,
        help_text="Only numbers with no SMS send/receive activity in this window are returned.",
    )
    include_detached_unused = forms.BooleanField(
        label="Detached unused",
        required=False,
        initial=True,
        help_text="Twilio numbers with no agent owner attached and no SMS activity in the selected window.",
    )
    include_free_dormant_unused = forms.BooleanField(
        label="Free-plan dormant unused",
        required=False,
        initial=True,
        help_text="Twilio numbers still attached to a free-plan owner with no SMS activity in the selected window.",
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("include_detached_unused") and not cleaned.get("include_free_dormant_unused"):
            raise forms.ValidationError("Select at least one candidate tier.")
        return cleaned


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


class BulkSetUserFlagsForm(forms.Form):
    user_ids = forms.CharField(
        label="User IDs",
        widget=forms.Textarea(
            attrs={"rows": 6, "placeholder": "Paste user IDs (integers), one per line or comma-separated"}
        ),
        help_text="List of user IDs (integers) to update.",
    )
    flag = forms.ModelChoiceField(
        label="Flag",
        queryset=UserFlagDefinition.objects.order_by("slug"),
        help_text="Configured user flag to set for the listed users.",
    )
    value = forms.TypedChoiceField(
        label="Value",
        choices=(
            ("true", "Enabled"),
            ("false", "Disabled"),
        ),
        coerce=lambda value: value == "true",
        help_text="Choose whether the selected flag should be enabled or disabled.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.invalid_user_id_tokens: list[str] = []

    def clean(self):
        cleaned = super().clean()
        flag = cleaned.get("flag")
        enabled = cleaned.get("value")
        if enabled and flag and flag.choice_options.filter(is_active=False).exists():
            raise forms.ValidationError(
                "Inactive user flag choice options cannot be enabled in bulk. "
                "Mark the option active first or choose a different flag."
            )
        return cleaned

    def clean_user_ids(self):
        raw = self.cleaned_data["user_ids"]
        tokens = [token for token in re.split(r"[\s,]+", raw.strip()) if token]
        if not tokens:
            raise forms.ValidationError("Enter at least one user ID.")

        parsed_ids: list[int] = []
        seen_ids: set[int] = set()
        invalid_tokens: list[str] = []

        for token in tokens:
            try:
                user_id = int(token)
            except ValueError:
                invalid_tokens.append(token)
                continue

            if user_id <= 0:
                invalid_tokens.append(token)
                continue

            if user_id in seen_ids:
                continue

            seen_ids.add(user_id)
            parsed_ids.append(user_id)

        if not parsed_ids:
            raise forms.ValidationError("Enter at least one valid user ID.")

        self.invalid_user_id_tokens = invalid_tokens
        return parsed_ids


class StripeConfigForm(ModelForm):
    """Admin form for managing Stripe configuration secrets."""

    webhook_secret = forms.CharField(
        label="Stripe webhook signing secret",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key.",
    )
    clear_webhook_secret = forms.BooleanField(
        label="Clear webhook secret",
        required=False,
        initial=False,
    )
    # Assigning these in the class namespace lets Django's form metaclass collect them.
    for _spec in STRIPE_CONFIG_FIELDS:
        locals()[_spec.name] = _stripe_config_form_field(_spec)
    del _spec

    class Meta:
        model = StripeConfig
        fields = (
            "release_env",
            "live_mode",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance: StripeConfig = self.instance
        if not instance or not instance.pk:
            return
        for spec in STRIPE_CONFIG_FIELDS:
            value = getattr(instance, spec.name)
            if spec.value_kind == StripeValueKind.STRING_LIST:
                value = ",".join(value or [])
            elif spec.value_kind == StripeValueKind.SINGULAR_WITH_LEGACY_LIST and not value:
                value = first_string(getattr(instance, spec.legacy_entry_name))
            self.fields[spec.name].initial = value

    def clean_release_env(self):
        value = self.cleaned_data.get("release_env", "")
        return value.strip()

    @staticmethod
    def _entry_value(raw_value):
        if raw_value is None:
            return None
        if isinstance(raw_value, str):
            return raw_value.strip() or None
        return str(raw_value)

    def save(self, commit: bool = True):
        instance: StripeConfig = super().save(commit=False)
        if instance.pk is None:
            if not commit:
                raise ValueError("StripeConfigForm.save(commit=False) is not supported for new configs")
            instance.save()

        webhook_secret = self.cleaned_data.get("webhook_secret")
        if self.cleaned_data.get("clear_webhook_secret"):
            instance.set_webhook_secret(None)
        elif webhook_secret:
            instance.set_webhook_secret(webhook_secret.strip())

        for spec in STRIPE_CONFIG_FIELDS:
            value = self._entry_value(self.cleaned_data.get(spec.name))
            instance.set_value(spec.name, value)
            if spec.legacy_entry_name:
                instance._set_list_value(spec.legacy_entry_name, value)

        if commit:
            instance.save()
            self.save_m2m()
        return instance
