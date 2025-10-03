from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class SuperuserSetupForm(forms.Form):
    email = forms.EmailField(
        label=_("Admin Email"),
        help_text=_("Used as the Django superuser login."),
    )
    password1 = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput,
        strip=False,
        min_length=8,
    )
    password2 = forms.CharField(
        label=_("Confirm Password"),
        widget=forms.PasswordInput,
        strip=False,
        min_length=8,
    )

    def clean(self):
        cleaned = super().clean()
        pw1 = cleaned.get("password1")
        pw2 = cleaned.get("password2")
        if pw1 and pw2 and pw1 != pw2:
            self.add_error("password2", _("Passwords do not match."))
        return cleaned


class LLMConfigForm(forms.Form):
    PROVIDER_OPENAI = "openai"
    PROVIDER_OPENROUTER = "openrouter"
    PROVIDER_ANTHROPIC = "anthropic"
    PROVIDER_FIREWORKS = "fireworks"
    PROVIDER_CUSTOM = "custom"

    PROVIDER_CHOICES = (
        (PROVIDER_OPENAI, _("OpenAI")),
        (PROVIDER_OPENROUTER, _("OpenRouter")),
        (PROVIDER_ANTHROPIC, _("Anthropic")),
        (PROVIDER_FIREWORKS, _("Fireworks.ai")),
        (PROVIDER_CUSTOM, _("Custom OpenAI-compatible endpoint")),
    )

    orchestrator_provider = forms.ChoiceField(
        label=_("Primary agents LLM"),
        choices=PROVIDER_CHOICES,
    )
    orchestrator_api_key = forms.CharField(
        label=_("API Key"),
        widget=forms.PasswordInput(render_value=True),
        help_text=_("Stored encrypted. Required unless you rely on environment variables."),
        required=False,
    )
    orchestrator_model = forms.CharField(
        label=_("Model Identifier"),
        required=False,
        help_text=_("LiteLLM model string, e.g. openai/gpt-4.1 or openai/gpt-4o."),
    )
    orchestrator_api_base = forms.CharField(
        label=_("API Base URL"),
        required=False,
        help_text=_("Required for custom OpenAI-compatible endpoints (e.g. http://localhost:8001/v1)."),
    )
    orchestrator_custom_name = forms.CharField(
        label=_("Custom Provider Name"),
        required=False,
        help_text=_("Displayed in the admin when using a custom endpoint."),
    )
    orchestrator_supports_tool_choice = forms.BooleanField(
        label=_("Supports tool choice"),
        required=False,
        initial=True,
    )
    orchestrator_use_parallel_tools = forms.BooleanField(
        label=_("Allow parallel tool calls"),
        required=False,
        initial=True,
    )

    browser_same_as_orchestrator = forms.BooleanField(
        label=_("Use the same provider for browser automations"),
        required=False,
        initial=True,
    )
    browser_provider = forms.ChoiceField(
        label=_("Browser automations LLM"),
        choices=PROVIDER_CHOICES,
        required=False,
    )
    browser_api_key = forms.CharField(
        label=_("Browser API Key"),
        widget=forms.PasswordInput(render_value=True),
        required=False,
    )
    browser_model = forms.CharField(
        label=_("Browser model identifier"),
        required=False,
        help_text=_("e.g. gpt-4o-mini or anthropic/claude-sonnet-4-20250514"),
    )
    browser_api_base = forms.CharField(
        label=_("Browser API base URL"),
        required=False,
    )
    browser_custom_name = forms.CharField(
        label=_("Browser custom provider name"),
        required=False,
    )

    def clean(self):
        cleaned = super().clean()

        orchestrator_provider = cleaned.get("orchestrator_provider")
        orchestrator_api_key = cleaned.get("orchestrator_api_key")
        orchestrator_model = cleaned.get("orchestrator_model")
        orchestrator_api_base = cleaned.get("orchestrator_api_base")

        if orchestrator_provider == self.PROVIDER_CUSTOM:
            if not orchestrator_api_base:
                self.add_error("orchestrator_api_base", _("API base URL is required for custom providers."))
            if not cleaned.get("orchestrator_custom_name"):
                self.add_error("orchestrator_custom_name", _("Provide a display name for the custom provider."))
            if not orchestrator_model:
                self.add_error("orchestrator_model", _("Provide the LiteLLM model identifier."))
        else:
            if not orchestrator_model:
                # Apply defaults in the view if left blank but no validation error.
                pass

        if orchestrator_provider != self.PROVIDER_CUSTOM and not orchestrator_api_key:
            # If the user truly wants to rely on env vars they can add them manually afterwards,
            # but we prevent empty key here to avoid failed first boot.
            self.add_error("orchestrator_api_key", _("Enter an API key for the selected provider."))

        if orchestrator_provider == self.PROVIDER_CUSTOM and not orchestrator_api_key:
            self.add_error("orchestrator_api_key", _("Custom providers require an API key (or token)."))

        browser_same = cleaned.get("browser_same_as_orchestrator")
        browser_provider = cleaned.get("browser_provider")
        browser_api_key = cleaned.get("browser_api_key")
        browser_model = cleaned.get("browser_model")
        browser_api_base = cleaned.get("browser_api_base")

        if not browser_same:
            if not browser_provider:
                self.add_error("browser_provider", _("Choose a provider for browser automations."))
            elif browser_provider == self.PROVIDER_CUSTOM:
                if not cleaned.get("browser_custom_name"):
                    self.add_error("browser_custom_name", _("Provide a name for the custom browser provider."))
                if not browser_api_base:
                    self.add_error("browser_api_base", _("Browser API base URL is required for custom providers."))
                if not browser_model:
                    self.add_error("browser_model", _("Provide a browser model identifier."))
                if not browser_api_key:
                    self.add_error("browser_api_key", _("Custom providers require an API key."))
            else:
                if not browser_model:
                    # allow default injection later
                    pass
                if not browser_api_key and orchestrator_provider != browser_provider:
                    # If the provider differs and no key provided, wizard can't proceed reliably.
                    self.add_error("browser_api_key", _("Enter an API key for the browser provider."))
        else:
            # When sharing provider we allow browser fields to stay blank (they will reuse orchestrator data)
            pass

        return cleaned
