from django import forms
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

from api.models import (
    ApiKey,
    MCPServerConfig,
    PersistentAgent,
    Organization,
    OrganizationMembership,
    OrganizationInvite,
)
from api.models import UserPhoneNumber
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.text import slugify
from django import forms
from django.core.exceptions import ValidationError

from constants.regex import E164_PHONE_REGEX
from constants.phone_countries import SUPPORTED_REGION_CODES
from util.phone import validate_and_format_e164
from api.models import CommsChannel
from util import sms
import logging

logger = logging.getLogger(__name__)


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

class UserPhoneNumberForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    phone_number = forms.CharField(
        max_length=32,
        validators=[RegexValidator(E164_PHONE_REGEX, "Enter a valid E.164 phone number")],
        widget=forms.TextInput(
            attrs={
                "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
                "placeholder": "+1234567890",
            }
        ),
        label="SMS Number",
    )
    verification_code = forms.CharField(
        max_length=10,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "block w-full px-4 py-3 text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500",
                "placeholder": "Verification code",
            }
        ),
        label="Verification Code",
    )

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get("phone_number")
        if not phone_number:
            return phone_number

        # Use shared validator to ensure consistent behavior and codes
        e164 = validate_and_format_e164(phone_number)

        if self.user and UserPhoneNumber.objects.filter(phone_number=e164).exclude(user=self.user).exists():
            raise forms.ValidationError("This phone number is already in use by another account.")
        return e164

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
    metadata = forms.JSONField(required=False, initial=dict, empty_value=dict, help_text="Additional JSON metadata (optional).")
    environment = forms.JSONField(required=False, initial=dict, empty_value=dict, help_text="JSON object of environment variables.")
    headers = forms.JSONField(required=False, initial=dict, empty_value=dict, help_text="JSON object of HTTP headers.")
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(
        self,
        *args,
        instance: MCPServerConfig | None = None,
        allow_commands: bool = True,
        **kwargs,
    ):
        self.instance = instance
        self.allow_commands = allow_commands
        initial = kwargs.setdefault('initial', {})
        if instance is not None:
            initial.setdefault('name', instance.name)
            initial.setdefault('display_name', instance.display_name)
            initial.setdefault('command', instance.command)
            initial.setdefault('url', instance.url)
            initial.setdefault('auth_method', instance.auth_method)
            initial.setdefault('command_args', instance.command_args or [])
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
        return value

    def clean_environment(self):
        value = self.cleaned_data.get('environment') or {}
        if not self.allow_commands:
            # User-managed servers cannot supply environment secrets via the console.
            return {}
        if not isinstance(value, dict):
            raise forms.ValidationError("Environment must be a JSON object.")
        return value

    def clean_headers(self):
        value = self.cleaned_data.get('headers') or {}
        if not isinstance(value, dict):
            raise forms.ValidationError("Headers must be a JSON object.")
        return value

    def save(self, *, user=None, organization=None) -> MCPServerConfig:
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

        config.name = self.cleaned_data['name']
        config.display_name = self.cleaned_data['display_name']
        config.command = self.cleaned_data.get('command', '')
        config.command_args = self.cleaned_data.get('command_args') or []
        config.url = self.cleaned_data.get('url', '')
        config.auth_method = self.cleaned_data.get('auth_method') or MCPServerConfig.AuthMethod.NONE
        if not self.allow_commands:
            config.command = ''
            config.command_args = []
        if 'prefetch_apps' in self.cleaned_data:
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


class PersistentAgentContactForm(forms.Form):
    """Form for step 2: contact preferences."""

    CONTACT_METHOD_CHOICES = [
        ('email', '📧 Email'),
        ('sms', '📱 SMS (New!)'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    preferred_contact_method = forms.ChoiceField(
        choices=CONTACT_METHOD_CHOICES,
        initial='email',
        required=True,
        widget=forms.Select(attrs={
            'class': 'py-3 ps-12 pe-4 block w-full rounded-xl border border-indigo-100 bg-white/90 text-base text-slate-700 shadow-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-indigo-100 transition-all duration-200 disabled:opacity-50 disabled:pointer-events-none'
        }),
        label='Preferred Contact Method',
        help_text='How would you like your agent to contact you?'
    )

    contact_endpoint_email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                'placeholder': 'your.email@example.com',
                'class': 'py-3 pe-4 ps-11 block w-full rounded-xl border border-indigo-100 bg-white/90 shadow-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-indigo-100 text-base text-slate-700 placeholder:text-slate-400 transition-all duration-200'
            },
        ),
        required=False,
        label='Your email address:',
        help_text='Once created, your agent will contact you at this address.'
    )

    email_enabled = forms.BooleanField(
        widget=forms.CheckboxInput(
            attrs={
                'class': 'sr-only peer',
                'checked': True,
                'disabled': False
            }
        ),
        initial=True,
        required=False,
    )

    sms_enabled = forms.BooleanField(
        widget=forms.CheckboxInput(
            attrs={
                'class': 'sr-only peer',
                'checked': False,
                'disabled': False
            }
        ),
        initial=False,
        required=False,
    )


    def clean_preferred_contact_method(self):
        contact_method = self.cleaned_data['preferred_contact_method']
        return contact_method

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get('preferred_contact_method')
        email_address = cleaned.get('contact_endpoint_email')

        if method == 'email' and not email_address:
            self.add_error('contact_endpoint_email', 'This field is required when email is selected.')

        return cleaned


# Keep the original form for backward compatibility
PersistentAgentForm = PersistentAgentContactForm


class PersistentAgentSecretsForm(forms.Form):
    """Form for managing persistent agent secrets."""
    
    def __init__(self, *args, agent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = agent
    
    def clean_secret_key(self, key):
        """Validate a single secret key."""
        if not key:
            raise forms.ValidationError("Secret key cannot be empty.")
        
        # Ensure key is alphanumeric with underscores only
        if not key.replace('_', '').isalnum():
            raise forms.ValidationError(f"Secret key '{key}' must be alphanumeric with underscores only.")
        
        # Ensure key doesn't start with a number
        if key[0].isdigit():
            raise forms.ValidationError(f"Secret key '{key}' cannot start with a number.")
        
        return key
    
    def clean_secret_value(self, value):
        """Validate a single secret value."""
        if not value:
            raise forms.ValidationError("Secret value cannot be empty.")
        
        if not isinstance(value, str):
            raise forms.ValidationError("Secret value must be a string.")
        
        return value


class AllowlistEntryForm(forms.Form):
    """Form to add a manual allowlist entry for an agent."""

    CHANNEL_CHOICES = [
        (CommsChannel.EMAIL, 'Email'),
        (CommsChannel.SMS, 'SMS'),
    ]

    channel = forms.ChoiceField(
        choices=CHANNEL_CHOICES,
        required=True,
        widget=forms.Select(attrs={
            'class': 'py-2 px-3 block w-full border-gray-300 rounded-lg text-sm focus:border-indigo-500 focus:ring-indigo-500'
        })
    )
    address = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'email@example.com or +15551234567',
            'class': 'py-2 px-3 block w-full border-gray-300 rounded-lg text-sm focus:border-indigo-500 focus:ring-indigo-500'
        }),
        label='Email or Phone',
        help_text='Emails are case-insensitive. Phone must be in E.164 (+15551234567).'
    )
    allow_inbound = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'rounded border-gray-300 text-indigo-600 focus:ring-indigo-500'
        }),
        label='Allow Inbound',
        help_text='Allow this contact to send messages to the agent'
    )
    allow_outbound = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'rounded border-gray-300 text-indigo-600 focus:ring-indigo-500'
        }),
        label='Allow Outbound',
        help_text='Allow the agent to send messages to this contact'
    )

    def clean(self):
        cleaned = super().clean()
        channel = cleaned.get('channel')
        address = (cleaned.get('address') or '').strip()
        if not address:
            self.add_error('address', 'Address is required.')
            return cleaned

        if channel == CommsChannel.EMAIL:
            if '@' not in address or '.' not in address.split('@')[-1]:
                self.add_error('address', 'Enter a valid email address.')
            cleaned['address'] = address.lower()
        elif channel == CommsChannel.SMS:
            from util.phone import validate_and_format_e164
            try:
                cleaned['address'] = validate_and_format_e164(address)
            except ValidationError as e:
                if getattr(e, 'code', None) == 'unsupported_region':
                    self.add_error('address', 'Phone numbers from this country are not yet supported.')
                else:
                    self.add_error('address', 'Enter a valid E.164 phone number (e.g., +15551234567).')
        else:
            self.add_error('channel', 'Unsupported channel.')

        return cleaned


class PersistentAgentAddSecretForm(forms.Form):
    """Form for adding a single secret to an agent."""
    
    domain = forms.CharField(
        max_length=256,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g., https://example.com, *.google.com, chrome-extension://abcd1234',
            'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none'
        }),
        label='Domain Pattern',
        help_text='Website domain pattern where this secret can be used. Required for security.'
    )
    
    name = forms.CharField(
        max_length=128,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g., X Password, API Key, Database Username',
            'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none'
        }),
        label='Secret Name',
        help_text='Human-readable name for this secret. The key will be generated automatically.'
    )
    
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'placeholder': 'Optional description of what this secret is used for...',
            'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none',
            'rows': 3
        }),
        label='Description',
        help_text='Optional description to help you remember what this secret is used for'
    )
    
    value = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter the secret value',
            'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none'
        }),
        label='Secret Value',
        help_text='This will be encrypted and stored securely'
    )
    
    def __init__(self, *args, agent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = agent
    
    def clean_domain(self):
        domain = self.cleaned_data['domain'].strip()
        
        try:
            from api.domain_validation import DomainPatternValidator
            from constants.security import SecretLimits
            
            # Additional length check with user-friendly message
            if len(domain) > SecretLimits.MAX_DOMAIN_PATTERN_LENGTH:
                raise forms.ValidationError(
                    f"Domain pattern is too long. Maximum {SecretLimits.MAX_DOMAIN_PATTERN_LENGTH} characters allowed."
                )
            
            DomainPatternValidator.validate_domain_pattern(domain)
            return DomainPatternValidator.normalize_domain_pattern(domain)
        except ValueError as e:
            raise forms.ValidationError(str(e))
    
    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        
        try:
            from constants.security import SecretLimits
            
            # Check length
            if len(name) > 128:
                raise forms.ValidationError(
                    f"Secret name is too long. Maximum 128 characters allowed."
                )
            
            if not name:
                raise forms.ValidationError("Secret name is required.")
            
            return name
        except ValueError as e:
            raise forms.ValidationError(str(e))
    
    def clean_value(self):
        value = self.cleaned_data['value']
        
        try:
            from api.domain_validation import DomainPatternValidator
            from constants.security import SecretLimits
            
            # Check size with user-friendly message
            value_bytes = len(value.encode('utf-8'))
            if value_bytes > SecretLimits.MAX_SECRET_VALUE_BYTES:
                raise forms.ValidationError(
                    f"Secret value is too large. Maximum {SecretLimits.MAX_SECRET_VALUE_BYTES} bytes allowed (current: {value_bytes} bytes)."
                )
            
            # Use comprehensive validation
            DomainPatternValidator._validate_secret_value(value)
            
            return value
        except ValueError as e:
            raise forms.ValidationError(str(e))
    
    def clean(self):
        cleaned_data = super().clean()
        domain = cleaned_data.get('domain')
        name = cleaned_data.get('name')
        
        if domain and name and self.agent:
            from api.models import PersistentAgentSecret
            from constants.security import SecretLimits
            
            # Check for duplicates by name (which is now the primary identifier)
            if PersistentAgentSecret.objects.filter(
                agent=self.agent,
                domain_pattern=domain,
                name=name
            ).exists():
                raise forms.ValidationError(f"Secret name '{name}' already exists for domain '{domain}'.")
            
            # Check limits before adding new secret
            total_secrets = PersistentAgentSecret.objects.filter(agent=self.agent).count()
            if total_secrets >= SecretLimits.MAX_SECRETS_PER_AGENT:
                raise forms.ValidationError(
                    f"Cannot add more secrets. Maximum {SecretLimits.MAX_SECRETS_PER_AGENT} secrets allowed per agent."
                )
            
            # Check domain limit
            distinct_domains = PersistentAgentSecret.objects.filter(
                agent=self.agent
            ).values('domain_pattern').distinct().count()
            
            if not PersistentAgentSecret.objects.filter(
                agent=self.agent, 
                domain_pattern=domain
            ).exists() and distinct_domains >= SecretLimits.MAX_DOMAINS_PER_AGENT:
                raise forms.ValidationError(
                    f"Cannot add more domains. Maximum {SecretLimits.MAX_DOMAINS_PER_AGENT} domains allowed per agent."
                )
        
        return cleaned_data


class PersistentAgentEditSecretForm(forms.Form):
    """Form for editing an existing secret."""
    
    name = forms.CharField(
        max_length=128,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g., X Password, API Key, Database Username',
            'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none'
        }),
        label='Secret Name',
        help_text='Human-readable name for this secret. The key will be updated automatically.'
    )
    
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'placeholder': 'Optional description of what this secret is used for...',
            'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none',
            'rows': 3
        }),
        label='Description',
        help_text='Optional description to help you remember what this secret is used for'
    )
    
    value = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter the new secret value',
            'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none'
        }),
        label='Secret Value',
        help_text='This will be encrypted and stored securely'
    )
    
    def __init__(self, *args, agent=None, secret=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = agent
        self.secret = secret
        
        # Pre-populate form with existing values if secret is provided
        if secret and not kwargs.get('data'):
            self.fields['name'].initial = secret.name
            self.fields['description'].initial = secret.description

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        
        try:
            if not name:
                raise forms.ValidationError("Secret name is required.")
            
            if len(name) > 128:
                raise forms.ValidationError(
                    f"Secret name is too long. Maximum 128 characters allowed."
                )
            
            return name
        except ValueError as e:
            raise forms.ValidationError(str(e))
    
    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        
        if name and self.agent and self.secret:
            from api.models import PersistentAgentSecret
            
            # Check for duplicates by name (excluding current secret)
            if PersistentAgentSecret.objects.filter(
                agent=self.agent,
                domain_pattern=self.secret.domain_pattern,
                name=name
            ).exclude(pk=self.secret.pk).exists():
                raise forms.ValidationError(f"Secret name '{name}' already exists for this domain.")
        
        return cleaned_data

    def clean_value(self):
        value = self.cleaned_data['value']
        
        try:
            from api.domain_validation import DomainPatternValidator
            from constants.security import SecretLimits
            
            # Check size with user-friendly message
            value_bytes = len(value.encode('utf-8'))
            if value_bytes > SecretLimits.MAX_SECRET_VALUE_BYTES:
                raise forms.ValidationError(
                    f"Secret value is too large. Maximum {SecretLimits.MAX_SECRET_VALUE_BYTES} bytes allowed (current: {value_bytes} bytes)."
                )
            
            # Use comprehensive validation
            DomainPatternValidator._validate_secret_value(value)
            
            return value
        except ValueError as e:
            raise forms.ValidationError(str(e))


class PersistentAgentSecretsRequestForm(forms.Form):
    """Form for providing values to multiple requested secrets at once."""
    
    def __init__(self, *args, requested_secrets=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.requested_secrets = requested_secrets or []
        
        # Dynamically create fields for each requested secret
        for secret in self.requested_secrets:
            field_name = f'secret_{secret.id}'
            self.fields[field_name] = forms.CharField(
                widget=forms.PasswordInput(attrs={
                    'placeholder': f'Enter value for {secret.name}',
                    'class': 'py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none'
                }),
                label=secret.name,
                help_text=secret.description if secret.description else f'Secret key: {secret.key}',
                required=False
            )
    
    def clean(self):
        cleaned_data = super().clean()
        
        # Validate all secret values
        for secret in self.requested_secrets:
            field_name = f'secret_{secret.id}'
            value = cleaned_data.get(field_name)
            
            if value:
                try:
                    from api.domain_validation import DomainPatternValidator
                    DomainPatternValidator._validate_secret_value(value)
                except Exception as e:
                    self.add_error(field_name, str(e))
        
        return cleaned_data


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


class ContactRequestApprovalForm(forms.Form):
    """Form for approving/rejecting contact requests."""
    
    def __init__(self, *args, contact_requests=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.contact_requests = contact_requests or []
        
        # Create checkbox fields for each request
        for request in self.contact_requests:
            # Approval checkbox
            field_name = f'approve_{request.id}'
            display_name = request.name or request.address
            self.fields[field_name] = forms.BooleanField(
                required=False,
                initial=True,  # Default to checked for convenience
                label=f"{display_name} ({request.channel})",
                help_text=f"Purpose: {request.purpose}",
                widget=forms.CheckboxInput(attrs={
                    'class': 'w-4 h-4 text-blue-600 bg-gray-100 border-gray-300 rounded focus:ring-blue-500'
                })
            )
            
            # Inbound permission checkbox
            inbound_field_name = f'inbound_{request.id}'
            self.fields[inbound_field_name] = forms.BooleanField(
                required=False,
                initial=request.request_inbound,  # Use the request's setting
                label="Allow receiving messages",
                widget=forms.CheckboxInput(attrs={
                    'class': 'w-4 h-4 text-blue-600 bg-gray-100 border-gray-300 rounded focus:ring-blue-500'
                })
            )
            
            # Outbound permission checkbox
            outbound_field_name = f'outbound_{request.id}'
            self.fields[outbound_field_name] = forms.BooleanField(
                required=False,
                initial=request.request_outbound,  # Use the request's setting
                label="Allow sending messages",
                widget=forms.CheckboxInput(attrs={
                    'class': 'w-4 h-4 text-blue-600 bg-gray-100 border-gray-300 rounded focus:ring-blue-500'
                })
            )

            # Configure permission checkbox
            configure_field_name = f'configure_{request.id}'
            self.fields[configure_field_name] = forms.BooleanField(
                required=False,
                initial=False,  # Default to no config authority
                label="Allow configuration changes",
                help_text="Can instruct agent to update charter/schedule",
                widget=forms.CheckboxInput(attrs={
                    'class': 'w-4 h-4 text-blue-600 bg-gray-100 border-gray-300 rounded focus:ring-blue-500'
                })
            )


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
