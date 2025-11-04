# admin_forms.py  (optional file)
from django import forms
from django.forms import ModelForm
from .models import CommsChannel, AgentEmailAccount, LLMProvider, StripeConfig, MCPServerConfig
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


class MCPServerConfigAdminForm(forms.ModelForm):
    """Admin form for managing platform-scoped MCP servers."""

    environment = forms.JSONField(
        required=False,
        help_text="Key/value environment variables passed to the MCP server process.",
    )
    headers = forms.JSONField(
        required=False,
        help_text="HTTP headers to include when invoking remote MCP servers.",
    )

    class Meta:
        model = MCPServerConfig
        fields = [
            "name",
            "display_name",
            "description",
            "auth_method",
            "command",
            "command_args",
            "url",
            "prefetch_apps",
            "metadata",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance and instance.pk:
            self.fields["environment"].initial = instance.environment
            self.fields["headers"].initial = instance.headers
            self.fields["auth_method"].initial = instance.auth_method
        else:
            self.fields["environment"].initial = {}
            self.fields["headers"].initial = {}
            self.fields["auth_method"].initial = MCPServerConfig.AuthMethod.NONE

    def clean_name(self):
        name = self.cleaned_data["name"]
        if name and name.strip().lower() != name:
            raise forms.ValidationError("Name must be lowercase and may not contain leading/trailing whitespace.")
        return name

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.scope = MCPServerConfig.Scope.PLATFORM
        obj.organization = None
        obj.user = None
        environment = self.cleaned_data.get("environment") or {}
        headers = self.cleaned_data.get("headers") or {}
        obj.environment = environment
        obj.headers = headers
        obj.auth_method = self.cleaned_data.get("auth_method") or MCPServerConfig.AuthMethod.NONE
        if commit:
            obj.save()
            self.save_m2m()
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


class LLMProviderForm(ModelForm):
    """Admin form for LLMProvider with write-only API key handling."""
    api_key = forms.CharField(
        label="Admin API Key",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key."
    )
    clear_api_key = forms.BooleanField(
        label="Clear stored admin API key",
        required=False,
        initial=False,
    )

    class Meta:
        model = LLMProvider
        fields = (
            "display_name",
            "key",
            "enabled",
            "env_var_name",
            "browser_backend",
            "supports_safety_identifier",
            "vertex_project",
            "vertex_location",
        )

    def clean(self):
        cleaned = super().clean()
        # Explicit uniqueness feedback for 'key' to avoid generic banner only
        key = cleaned.get("key")
        if key:
            qs = LLMProvider.objects.filter(key=key)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("key", "A provider with this key already exists.")

        # Allow providers without any key (admin or env) — no validation required here.
        # Vertex fields are optional and only used if backend == GOOGLE (no strict enforcement).
        # Ensure display_name and key are non-empty strings
        if not cleaned.get("display_name"):
            self.add_error("display_name", "Display name is required.")
        if not cleaned.get("key"):
            self.add_error("key", "Key is required.")
        return cleaned

    def save(self, commit=True):
        instance: LLMProvider = super().save(commit=False)
        api_key = self.cleaned_data.get("api_key")
        clear = self.cleaned_data.get("clear_api_key")
        if clear:
            instance.api_key_encrypted = None
        elif api_key:
            from .encryption import SecretsEncryption
            instance.api_key_encrypted = SecretsEncryption.encrypt_value(api_key)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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
    startup_price_id = forms.CharField(
        label="Startup base price ID",
        required=False,
    )
    startup_additional_task_price_id = forms.CharField(
        label="Startup additional task price ID",
        required=False,
    )
    startup_product_id = forms.CharField(
        label="Startup product ID",
        required=False,
    )
    scale_price_id = forms.CharField(
        label="Scale base price ID",
        required=False,
    )
    scale_additional_task_price_id = forms.CharField(
        label="Scale additional task price ID",
        required=False,
    )
    scale_product_id = forms.CharField(
        label="Scale product ID",
        required=False,
    )
    startup_dedicated_ip_product_id = forms.CharField(
        label="Pro dedicated IP product ID",
        required=False,
    )
    startup_dedicated_ip_price_id = forms.CharField(
        label="Pro dedicated IP price ID",
        required=False,
    )
    scale_dedicated_ip_product_id = forms.CharField(
        label="Scale dedicated IP product ID",
        required=False,
    )
    scale_dedicated_ip_price_id = forms.CharField(
        label="Scale dedicated IP price ID",
        required=False,
    )
    org_team_product_id = forms.CharField(
        label="Org/Team product ID",
        required=False,
    )
    org_team_price_id = forms.CharField(
        label="Org/Team price ID",
        required=False,
    )
    org_team_additional_task_price_id = forms.CharField(
        label="Org/Team additional task price ID",
        required=False,
    )
    org_team_dedicated_ip_product_id = forms.CharField(
        label="Org/Team dedicated IP product ID",
        required=False,
    )
    org_team_dedicated_ip_price_id = forms.CharField(
        label="Org/Team dedicated IP price ID",
        required=False,
    )
    task_meter_id = forms.CharField(
        label="Task meter ID",
        required=False,
    )
    task_meter_event_name = forms.CharField(
        label="Task meter event name",
        required=False,
    )
    org_task_meter_id = forms.CharField(
        label="Organization task meter ID",
        required=False,
    )
    org_team_task_meter_id = forms.CharField(
        label="Org/Team task meter ID",
        required=False,
    )
    org_team_task_meter_event_name = forms.CharField(
        label="Org/Team task meter event name",
        required=False,
    )

    class Meta:
        model = StripeConfig
        fields = (
            "release_env",
            "live_mode",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance: StripeConfig = self.instance
        if instance and instance.pk:
            self.fields["startup_price_id"].initial = instance.startup_price_id
            self.fields["startup_additional_task_price_id"].initial = instance.startup_additional_task_price_id
            self.fields["startup_product_id"].initial = instance.startup_product_id
            self.fields["scale_price_id"].initial = instance.scale_price_id
            self.fields["scale_additional_task_price_id"].initial = instance.scale_additional_task_price_id
            self.fields["scale_product_id"].initial = instance.scale_product_id
            self.fields["startup_dedicated_ip_product_id"].initial = instance.startup_dedicated_ip_product_id
            self.fields["startup_dedicated_ip_price_id"].initial = instance.startup_dedicated_ip_price_id
            self.fields["scale_dedicated_ip_product_id"].initial = instance.scale_dedicated_ip_product_id
            self.fields["scale_dedicated_ip_price_id"].initial = instance.scale_dedicated_ip_price_id
            self.fields["org_team_product_id"].initial = instance.org_team_product_id
            self.fields["org_team_price_id"].initial = instance.org_team_price_id
            self.fields["org_team_additional_task_price_id"].initial = instance.org_team_additional_task_price_id
            self.fields["org_team_dedicated_ip_product_id"].initial = instance.org_team_dedicated_ip_product_id
            self.fields["org_team_dedicated_ip_price_id"].initial = instance.org_team_dedicated_ip_price_id
            self.fields["task_meter_id"].initial = instance.task_meter_id
            self.fields["task_meter_event_name"].initial = instance.task_meter_event_name
            self.fields["org_task_meter_id"].initial = instance.org_task_meter_id
            self.fields["org_team_task_meter_id"].initial = instance.org_team_task_meter_id
            self.fields["org_team_task_meter_event_name"].initial = instance.org_team_task_meter_event_name

    def clean_release_env(self):
        value = self.cleaned_data.get("release_env", "")
        return value.strip()

    def save(self, commit: bool = True):
        instance: StripeConfig = super().save(commit=False)

        if instance.pk is None:
            if not commit:
                raise ValueError("StripeConfigForm.save(commit=False) is not supported for new configs")
            instance.save()

        secrets_to_process = [
            ("webhook_secret", "clear_webhook_secret", instance.set_webhook_secret),
        ]
        for secret_field, clear_field, setter_method in secrets_to_process:
            secret_value = self.cleaned_data.get(secret_field)
            if self.cleaned_data.get(clear_field):
                setter_method(None)
            elif secret_value:
                setter_method(secret_value.strip())

        simple_fields = [
            "startup_price_id",
            "startup_additional_task_price_id",
            "startup_product_id",
            "scale_price_id",
            "scale_additional_task_price_id",
            "scale_product_id",
            "startup_dedicated_ip_product_id",
            "startup_dedicated_ip_price_id",
            "scale_dedicated_ip_product_id",
            "scale_dedicated_ip_price_id",
            "org_team_product_id",
            "org_team_price_id",
            "org_team_additional_task_price_id",
            "org_team_dedicated_ip_product_id",
            "org_team_dedicated_ip_price_id",
            "task_meter_id",
            "task_meter_event_name",
            "org_task_meter_id",
            "org_team_task_meter_id",
            "org_team_task_meter_event_name",
        ]
        for field_name in simple_fields:
            value = self.cleaned_data.get(field_name)
            instance.set_value(field_name, (value or "").strip() or None)

        if commit:
            instance.save()
            self.save_m2m()
        return instance
