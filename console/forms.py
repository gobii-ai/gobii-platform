from django import forms
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from functools import lru_cache

from api.models import (
    ApiKey,
    MCPServerConfig,
    PersistentAgent,
    Organization,
    OrganizationMembership,
    OrganizationInvite,
    UserPreference,
)
from api.models import UserPhoneNumber
from django.utils import timezone
from django.utils.text import slugify
from django.core.exceptions import ValidationError

from constants.phone_countries import SUPPORTED_REGION_CODES
from api.services.user_timezone import normalize_timezone_value, resolve_user_timezone
from api.services.mcp_config_validation import (
    validate_environment_mapping,
    validate_mcp_metadata_environment_references,
)
from util import sms
import logging

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _build_timezone_choices() -> tuple[tuple[str, str], ...]:
    return (
        ("", "Auto-detect (from browser)"),
        ("UTC", "UTC"),
        ("America/New_York", "Eastern Time (US & Canada)"),
        ("America/Chicago", "Central Time (US & Canada)"),
        ("America/Denver", "Mountain Time (US & Canada)"),
        ("America/Los_Angeles", "Pacific Time (US & Canada)"),
        ("America/Phoenix", "Arizona"),
        ("America/Anchorage", "Alaska"),
        ("Pacific/Honolulu", "Hawaii"),
        ("Europe/London", "London"),
        ("Europe/Paris", "Paris"),
        ("Europe/Berlin", "Berlin"),
        ("Asia/Tokyo", "Tokyo"),
        ("Asia/Kolkata", "India"),
        ("Australia/Sydney", "Sydney"),
    )


class DedicatedIpAddForm(forms.Form):
    quantity = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "class": "py-2 px-2 w-20 border-gray-200 rounded-lg text-sm text-center focus:border-blue-500 focus:ring-blue-500",
                "inputmode": "numeric",
                "max": "99",
            }
        ),
        label="Add Dedicated IPs",
        help_text="Enter how many new dedicated IPs to add.",
        initial=1,
    )


class AddonQuantityForm(forms.Form):
    quantity = forms.IntegerField(
        min_value=0,
        max_value=999,
        widget=forms.NumberInput(
            attrs={
                "class": "py-2 px-2 w-24 border-gray-200 rounded-lg text-sm text-center focus:border-blue-500 focus:ring-blue-500",
                "inputmode": "numeric",
                "max": "999",
            }
        ),
        label="Quantity",
        help_text="Total units to keep on your subscription.",
        initial=0,
    )
    price_id = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, label: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if label:
            self.fields["quantity"].label = label


class ApiKeyForm(forms.ModelForm):
    class Meta:
        model = ApiKey
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500',
                'placeholder': 'Enter API key name'
            })
        }

    def __init__(self, *args, user=None, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.organization = organization

        if user is not None:
            self.instance.user = user
            self.instance.organization = None
        elif organization is not None:
            self.instance.organization = organization
            self.instance.user = None

    def clean(self):
        cleaned = super().clean()
        if self.user and self.organization:
            raise forms.ValidationError("API keys must belong to exactly one owner.")
        if not self.user and not self.organization:
            raise forms.ValidationError("Unable to determine API key owner. Refresh and try again.")
        return cleaned

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()

        filters = {}
        if self.user:
            filters["user"] = self.user
        if self.organization:
            filters["organization"] = self.organization

        if ApiKey.objects.filter(**filters, name__iexact=name).exists():
            raise forms.ValidationError("An API key with that name already exists.")
        return name


class UserProfileForm(forms.ModelForm):
    timezone = forms.ChoiceField(
        required=False,
        label="Timezone",
        choices=(),
        widget=forms.Select(
            attrs={
                "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
            }
        ),
    )

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name"]
        widgets = {
            "first_name": forms.TextInput(
                attrs={
                    "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        timezone_choices = list(_build_timezone_choices())
        known_values = {value for value, _label in timezone_choices}

        initial_timezone = ""
        if getattr(self.instance, "pk", None):
            initial_timezone = resolve_user_timezone(
                self.instance,
                fallback_to_utc=False,
            )
        if initial_timezone and initial_timezone not in known_values:
            timezone_choices.append((initial_timezone, f"{initial_timezone} (stored)"))

        self.fields["timezone"].choices = timezone_choices
        self.fields["timezone"].initial = initial_timezone

    def clean_timezone(self):
        timezone_value = self.cleaned_data.get("timezone", "")
        return normalize_timezone_value(
            timezone_value,
            key=UserPreference.KEY_USER_TIMEZONE,
        )

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit and getattr(user, "pk", None):
            timezone_value = self.cleaned_data.get("timezone", "")
            UserPreference.update_known_preferences(
                user,
                {UserPreference.KEY_USER_TIMEZONE: timezone_value},
            )
        return user

class StyledRadioSelect(forms.RadioSelect):
    """Custom RadioSelect widget with Preline styling."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attrs = {
            'class': 'shrink-0 mt-0.5 border-gray-300 rounded-full text-indigo-600 focus:ring-indigo-500 checked:border-indigo-500 disabled:opacity-50 disabled:pointer-events-none'
        }

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)

        return option


class MCPServerConfigForm(forms.Form):
    name = forms.SlugField(
        max_length=64,
        help_text="Short identifier used by agents (lowercase letters, numbers, and hyphens).",
        required=False,
    )
    display_name = forms.CharField(max_length=128)
    command = forms.CharField(max_length=255, required=False, help_text="Executable to launch (leave blank for HTTP servers).")
    url = forms.CharField(max_length=512, required=False, help_text="HTTP/S URL for remote MCP servers.")
    auth_method = forms.ChoiceField(
        choices=MCPServerConfig.AuthMethod.choices,
        initial=MCPServerConfig.AuthMethod.NONE,
        help_text="Select how Gobii should authenticate requests to this MCP server.",
    )
    command_args = forms.JSONField(required=False, initial=list, empty_value=list, help_text="JSON array of command arguments, e.g. ['-y', '@pkg@1.0.0'].")
    prefetch_apps = forms.JSONField(required=False, initial=list, empty_value=list, help_text="JSON array of Pipedream app slugs to prefetch.")
    metadata = forms.JSONField(required=False, initial=dict, empty_value=dict, help_text="Additional JSON metadata (optional).")
    environment = forms.JSONField(required=False, initial=dict, empty_value=dict, help_text="JSON object of environment variables.")
    headers = forms.JSONField(required=False, initial=dict, empty_value=dict, help_text="JSON object of HTTP headers.")
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(
        self,
        *args,
        instance: MCPServerConfig | None = None,
        allow_commands: bool = True,
        allow_prefetch_apps: bool = False,
        **kwargs,
    ):
        self.instance = instance
        self.allow_commands = allow_commands
        self.allow_prefetch_apps = allow_prefetch_apps
        initial = kwargs.setdefault('initial', {})
        if instance is not None:
            initial.setdefault('name', instance.name)
            initial.setdefault('display_name', instance.display_name)
            initial.setdefault('command', instance.command)
            initial.setdefault('url', instance.url)
            initial.setdefault('auth_method', instance.auth_method)
            initial.setdefault('command_args', instance.command_args or [])
            initial.setdefault('prefetch_apps', instance.prefetch_apps or [])
            initial.setdefault('metadata', instance.metadata or {})
            initial.setdefault('environment', instance.environment or {})
            initial.setdefault('headers', instance.headers or {})
            initial.setdefault('is_active', instance.is_active)
        if not allow_commands:
            initial.setdefault('command', '')
            initial.setdefault('command_args', [])
        super().__init__(*args, **kwargs)

        self.fields['name'].widget = forms.HiddenInput()
        self.fields['name'].widget.attrs.update({'x-model': 'slug', 'x-bind:value': 'slug'})

        display_widget = self.fields['display_name'].widget
        display_widget.attrs.setdefault('x-model', 'displayName')
        if instance is None:
            display_widget.attrs.setdefault('x-on:input', 'slug = slugify($event.target.value)')
        self.fields['url'].widget.attrs.setdefault('x-ref', 'serverUrl')
        auth_widget = self.fields['auth_method'].widget
        auth_widget.attrs.setdefault('x-ref', 'authMethod')
        auth_widget.attrs.setdefault('x-model', 'authMethodValue')

        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.HiddenInput):
                continue
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.update({'class': 'h-4 w-4 text-blue-600 border-gray-300 rounded'})
            elif isinstance(widget, forms.Textarea):
                widget.attrs.setdefault('rows', 3)
                widget.attrs.update({'class': 'py-2 px-3 block w-full border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500 font-mono text-sm'})
            else:
                widget.attrs.update({'class': 'py-2 px-3 block w-full border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500'})
        headers_widget = forms.HiddenInput()
        headers_widget.attrs.update({'x-ref': 'headersField'})
        self.fields['headers'].widget = headers_widget
        if not self.allow_commands:
            self.fields['command'].widget = forms.HiddenInput()
            self.fields['command_args'].widget = forms.HiddenInput()
            self.fields['environment'].widget = forms.HiddenInput()
            self.fields['metadata'].widget = forms.HiddenInput()
        if not self.allow_prefetch_apps:
            self.fields['prefetch_apps'].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()
        command = (cleaned.get('command') or '').strip()
        url = (cleaned.get('url') or '').strip()
        reserved = {name.lower() for name in MCPServerConfig.RESERVED_PLATFORM_NAMES}
        if not self.allow_commands:
            errors: dict[str, str] = {}
            if command:
                errors['command'] = "Command-based MCP servers are managed by Gobii. Provide a URL instead."
            if cleaned.get('command_args'):
                errors['command_args'] = "Command arguments are not supported for user-managed MCP servers."
            if not url:
                errors['url'] = "Provide a URL for the MCP server."
            cleaned['command'] = ''
            cleaned['command_args'] = []
            if errors:
                raise forms.ValidationError(errors)
        elif not command and not url:
            raise forms.ValidationError("Provide either a command or a URL for the MCP server.")

        name = (cleaned.get('name') or '').strip()
        display_name = (cleaned.get('display_name') or '').strip()
        if not name and display_name:
            generated = slugify(display_name)
            cleaned['name'] = generated[:64]
        if not cleaned.get('name'):
            raise forms.ValidationError("Unable to generate an identifier. Add a display name with letters or numbers.")
        if cleaned['name'].lower() in reserved and self.allow_commands is False:
            raise forms.ValidationError("This MCP server identifier is reserved for Gobii-managed integrations.")
        return cleaned

    def clean_command_args(self):
        value = self.cleaned_data.get('command_args') or []
        if not isinstance(value, list):
            raise forms.ValidationError("Command arguments must be a JSON array.")
        return value

    def clean_metadata(self):
        value = self.cleaned_data.get('metadata') or {}
        if not self.allow_commands:
            return {}
        if not isinstance(value, dict):
            raise forms.ValidationError("Metadata must be a JSON object.")
        metadata_errors = validate_mcp_metadata_environment_references(value)
        if metadata_errors:
            raise forms.ValidationError(metadata_errors)
        return value

    def clean_prefetch_apps(self):
        value = self.cleaned_data.get('prefetch_apps') or []
        if not isinstance(value, list):
            raise forms.ValidationError("Prefetch apps must be a JSON array.")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise forms.ValidationError("Prefetch apps must be app slug strings.")
            slug = item.strip().lower().replace(" ", "_")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            normalized.append(slug)
        return normalized

    def clean_environment(self):
        value = self.cleaned_data.get('environment') or {}
        if not self.allow_commands:
            # User-managed servers cannot supply environment secrets via the console.
            return {}
        if not isinstance(value, dict):
            raise forms.ValidationError("Environment must be a JSON object.")
        environment_errors = validate_environment_mapping(value)
        if environment_errors:
            raise forms.ValidationError(environment_errors)
        return value

    def clean_headers(self):
        value = self.cleaned_data.get('headers') or {}
        if not isinstance(value, dict):
            raise forms.ValidationError("Headers must be a JSON object.")
        return value

    def save(self, *, user=None, organization=None, platform: bool = False) -> MCPServerConfig:
        if self.instance is None:
            config = MCPServerConfig()
            if organization is not None:
                config.scope = MCPServerConfig.Scope.ORGANIZATION
                config.organization = organization
            else:
                config.scope = MCPServerConfig.Scope.USER
                config.user = user
        else:
            config = self.instance

        if platform:
            config.scope = MCPServerConfig.Scope.PLATFORM
            config.organization = None
            config.user = None

        config.name = self.cleaned_data['name']
        config.display_name = self.cleaned_data['display_name']
        config.command = self.cleaned_data.get('command', '')
        config.command_args = self.cleaned_data.get('command_args') or []
        config.url = self.cleaned_data.get('url', '')
        config.auth_method = self.cleaned_data.get('auth_method') or MCPServerConfig.AuthMethod.NONE
        if not self.allow_commands:
            config.command = ''
            config.command_args = []
        if self.allow_prefetch_apps and 'prefetch_apps' in self.cleaned_data:
            config.prefetch_apps = self.cleaned_data.get('prefetch_apps') or []
        config.metadata = self.cleaned_data.get('metadata') or {}
        config.is_active = bool(self.cleaned_data.get('is_active'))
        config.environment = self.cleaned_data.get('environment') or {}
        config.headers = self.cleaned_data.get('headers') or {}

        config.save()
        return config


class PersistentAgentCharterForm(forms.Form):
    """Form for step 1: defining what the agent should do."""
    
    charter = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': '',
            'class': 'block w-full bg-transparent border-none focus:ring-0 text-base px-5 py-3.5 resize-none placeholder:text-gray-400',
            'oninput': 'textareaAutoResize(this)',
            'data-max-height': '400',
            'style': 'height:auto;overflow:hidden;'
        }),
        label='',
        required=False,
        help_text='Describe what you want your persistent agent to do'
    )

    def clean_charter(self):
        return (self.cleaned_data.get('charter') or '').strip()

    DEFAULT_CHARTER = (
        "Have a friendly conversation with the user to understand what they need help with, "
        "then adapt to assist them."
    )


class AgentEmailAccountConsoleForm(forms.Form):
    """Lightweight console form to edit BYO email settings.

    Keeps passwords write-only; leaves existing if blank.
    """

    # SMTP
    smtp_host = forms.CharField(required=False)
    smtp_port = forms.IntegerField(required=False)
    smtp_security = forms.ChoiceField(
        choices=[('ssl', 'SSL'), ('starttls', 'STARTTLS'), ('none', 'None')], required=False, initial='starttls'
    )
    smtp_auth = forms.ChoiceField(
        choices=[('none', 'None'), ('plain', 'PLAIN'), ('login', 'LOGIN'), ('oauth2', 'OAuth 2.0')], required=False, initial='login'
    )
    smtp_username = forms.CharField(required=False)
    smtp_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    is_outbound_enabled = forms.BooleanField(required=False, initial=False)
    connection_mode = forms.ChoiceField(
        choices=[('custom', 'Custom SMTP/IMAP'), ('oauth2', 'OAuth 2.0')], required=False, initial='oauth2'
    )

    # IMAP
    imap_host = forms.CharField(required=False)
    imap_port = forms.IntegerField(required=False)
    imap_security = forms.ChoiceField(
        choices=[('ssl', 'SSL'), ('starttls', 'STARTTLS'), ('none', 'None')], required=False, initial='ssl'
    )
    imap_username = forms.CharField(required=False)
    imap_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    imap_auth = forms.ChoiceField(
        choices=[('none', 'None'), ('login', 'LOGIN'), ('oauth2', 'OAuth 2.0')], required=False, initial='login'
    )
    imap_folder = forms.CharField(required=False, initial='INBOX')
    is_inbound_enabled = forms.BooleanField(required=False, initial=False)
    imap_idle_enabled = forms.BooleanField(required=False, initial=False)

    poll_interval_sec = forms.IntegerField(required=False, initial=120, min_value=30)

    def clean(self):
        cleaned = super().clean()
        connection_mode = cleaned.get('connection_mode') or 'custom'
        if connection_mode == 'oauth2':
            return cleaned
        if cleaned.get('is_outbound_enabled'):
            for f in ('smtp_host', 'smtp_security', 'smtp_auth'):
                if not cleaned.get(f):
                    self.add_error(f, 'Required when outbound is enabled')
            if cleaned.get('smtp_auth') and cleaned.get('smtp_auth') != 'none':
                if not cleaned.get('smtp_username'):
                    self.add_error('smtp_username', 'Username required for authenticated SMTP')
        # Minimal IMAP validation only when enabling inbound
        if cleaned.get('is_inbound_enabled'):
            for f in ('imap_host', 'imap_security', 'imap_username'):
                if not cleaned.get(f):
                    self.add_error(f, 'Required when inbound is enabled')
        return cleaned


class PhoneAddForm(forms.Form):
    phone_number = forms.CharField(
        label="Phone number",
        widget = forms.TextInput(
            attrs={
                "class": "phone_number w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 py-3 px-4 ps-11 block w-full rounded-xl border-gray-300 bg-white/80 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 text-base placeholder:text-gray-400",
                "type": "tel",
                "autocomplete": "tel",
                "placeholder": "Enter phone",
                "id": "phone_number_input",
            }
        ),
    )

    phone_number_hidden = forms.CharField(
        widget=forms.HiddenInput(
            attrs={
                "id": "phone_number_hidden"
            },
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        phone_raw = cleaned.get("phone_number_hidden") or cleaned.get("phone_number")
        if not phone_raw:
            return cleaned

        # Validate format and supported country (shared util)
        from util.phone import validate_and_format_e164
        validate_and_format_e164(phone_raw)

        return cleaned

    def save(self):
        phone_raw = self.cleaned_data["phone_number_hidden"]
        # Todo: error handling

        # Convert to E.164 using shared util
        from django.utils import timezone
        from util.phone import validate_and_format_e164

        try:
            phone_formatted = validate_and_format_e164(phone_raw)
        except ValidationError as e:
            raise e

        try:
            phone, created = UserPhoneNumber.objects.get_or_create(
                user=self.user,
                phone_number=phone_formatted,
                defaults={
                    'is_verified': False,
                    'is_primary': True,  # Set as primary if it's a new phone, and we only support one phone *for now*
                    'verified_at': None,
                    'created_at': timezone.now(),
                    'updated_at': timezone.now(),
                }
            )
        except IntegrityError as e:
            logger.error(f"Integrity error saving phone number: {str(e)}")
            raise e
        except Exception as e:
            raise ValidationError(f"Error saving phone number: {str(e)}")

        # Go ahead and send verification
        try:
            sid = sms.start_verification(phone_number=phone_formatted)
            phone.last_verification_attempt = timezone.now()
            phone.verification_sid = sid
            phone.save(update_fields=["last_verification_attempt", "verification_sid", "updated_at"])
        except Exception as e:
            logger.error(f"Error sending verification: {str(e)}")
            raise ValidationError(f"Error sending verification: {str(e)}")

        return phone

class PhoneVerifyForm(forms.Form):
    phone_number = forms.CharField(widget=forms.HiddenInput)  # stays in the POST
    verification_code = forms.CharField(
        max_length=6,
        label="Verification Code",
        required=True,
        widget=forms.TextInput(
            attrs={
                "class": "px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
            }
        )
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()

        code = cleaned.get("verification_code")
        phone_number = cleaned.get("phone_number")
        # Avoid calling provider when code is missing
        if not code:
            raise ValidationError("Verification code is required.")

        verified = sms.check_verification(phone_number=phone_number, code=code)

        if not verified:
             raise ValidationError("Incorrect or expired code.")

        return cleaned

    def save(self):
        phone_number = self.cleaned_data["phone_number"]

        phone = UserPhoneNumber.objects.filter(
            user=self.user,
            phone_number=phone_number,
        ).first()

        if phone:
            phone.is_verified = True
            phone.verified_at = timezone.now()
            phone.save()
            return phone
        else:
            raise ValidationError("Phone number not found for this user.")


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
                    "placeholder": "Organization name",
                }
            )
        }


class OrganizationInviteForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
                "placeholder": "user@example.com",
            }
        )
    )
    role = forms.ChoiceField(
        choices=OrganizationMembership.OrgRole.choices,
        widget=forms.Select(
            attrs={
                "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
            }
        ),
    )

    def __init__(self, *args, org=None, allowed_roles=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        if allowed_roles is not None:
            self.fields["role"].choices = allowed_roles

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not self.org or not email:
            return email

        if OrganizationMembership.objects.filter(
            org=self.org,
            user__email__iexact=email,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exists():
            raise forms.ValidationError('This user is already an active member of this organization.')

        now = timezone.now()
        if OrganizationInvite.objects.filter(
            org=self.org,
            email__iexact=email,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gte=now,
        ).exists():
            raise forms.ValidationError('This email already has a pending invitation.')

        return email

    def clean(self):
        cleaned = super().clean()

        if not self.org:
            return cleaned

        billing = getattr(self.org, "billing", None)
        if billing is None:
            raise forms.ValidationError('Organization billing configuration is missing for this organization.')

        role = cleaned.get("role")
        if role == OrganizationMembership.OrgRole.SOLUTIONS_PARTNER:
            return cleaned

        if billing.seats_available <= 0:
            raise forms.ValidationError('No seats available. Increase the seat count before inviting new members.')

        return cleaned


class OrganizationSeatReductionForm(forms.Form):
    future_seats = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(
            attrs={
                "class": "w-28 px-3 py-2 border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500",
                "min": "0",
            }
        ),
        label="Seats next cycle",
    )

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org

        billing = getattr(org, "billing", None)
        if billing is not None:
            purchased = getattr(billing, "purchased_seats", 0) or 0
            self.fields["future_seats"].initial = purchased
            self.fields["future_seats"].widget.attrs["max"] = str(max(purchased, 0))

    def clean_future_seats(self):
        seats = self.cleaned_data.get("future_seats")
        if seats is None:
            return seats

        billing = getattr(self.org, "billing", None)
        if billing is None or not getattr(billing, "stripe_subscription_id", None):
            raise forms.ValidationError("This organization does not have an active subscription to update.")

        current = getattr(billing, "purchased_seats", 0) or 0
        if seats >= current:
            raise forms.ValidationError("Enter a number smaller than your current seat total to schedule a reduction.")

        reserved = billing.seats_reserved
        if seats < reserved:
            raise forms.ValidationError(
                "Cannot schedule fewer seats than currently reserved. Remove members or invites first."
            )

        return seats

class OrganizationSeatPurchaseForm(forms.Form):
    seats = forms.IntegerField(
        min_value=1,
        initial=1,
        widget=forms.NumberInput(
            attrs={
                "class": "w-24 px-3 py-2 border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500",
                "min": "1",
            }
        ),
        label="Seats",
    )

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
