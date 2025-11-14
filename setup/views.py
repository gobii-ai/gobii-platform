from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Optional, Tuple

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.db import OperationalError, connections, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views import View
from django.views.decorators.http import require_POST

from api.agent.core.llm_utils import run_completion
from api.openrouter import get_attribution_headers

from api.agent.core.llm_config import (
    invalidate_llm_bootstrap_cache,
    get_required_temperature_for_model,
)
from api.encryption import SecretsEncryption
from api.models import (
    LLMProvider,
    PersistentModelEndpoint,
    PersistentLLMTier,
    PersistentTierEndpoint,
    PersistentTokenRange,
    BrowserModelEndpoint,
    BrowserLLMPolicy,
    BrowserLLMTier,
    BrowserTierEndpoint,
)

from .forms import LLMConfigForm, SuperuserSetupForm
from .middleware import is_initial_setup_complete

logger = logging.getLogger(__name__)

DEFAULT_ORCHESTRATOR_MODELS = {
    LLMConfigForm.PROVIDER_OPENAI: "gpt-4.1",
    LLMConfigForm.PROVIDER_OPENROUTER: "z-ai/glm-4.6:exacto",
    LLMConfigForm.PROVIDER_ANTHROPIC: "claude-sonnet-4-20250514",
    LLMConfigForm.PROVIDER_FIREWORKS: "accounts/fireworks/models/gpt-oss-120b",
}

DEFAULT_BROWSER_MODELS = {
    LLMConfigForm.PROVIDER_OPENAI: "gpt-4o-mini",
    LLMConfigForm.PROVIDER_OPENROUTER: "z-ai/glm-4.6:exacto",
    LLMConfigForm.PROVIDER_ANTHROPIC: "claude-sonnet-4-20250514",
    LLMConfigForm.PROVIDER_FIREWORKS: "accounts/fireworks/models/qwen3-235b-a22b-instruct-2507",
}

DEFAULT_BROWSER_BASE_URLS = {
    LLMConfigForm.PROVIDER_OPENROUTER: "https://openrouter.ai/api/v1",
    LLMConfigForm.PROVIDER_FIREWORKS: "https://api.fireworks.ai/inference/v1",
}
DEFAULT_PROVIDER_API_BASES = {
    LLMConfigForm.PROVIDER_OPENAI: "https://api.openai.com/v1",
    LLMConfigForm.PROVIDER_OPENROUTER: DEFAULT_BROWSER_BASE_URLS.get(LLMConfigForm.PROVIDER_OPENROUTER, ""),
    LLMConfigForm.PROVIDER_FIREWORKS: DEFAULT_BROWSER_BASE_URLS.get(LLMConfigForm.PROVIDER_FIREWORKS, ""),
}

ORCHESTRATOR_ENDPOINT_KEYS = {
    LLMConfigForm.PROVIDER_OPENAI: "openai_gpt4_1",
    LLMConfigForm.PROVIDER_OPENROUTER: "openrouter_glm_45",
    LLMConfigForm.PROVIDER_ANTHROPIC: "anthropic_sonnet4",
    LLMConfigForm.PROVIDER_FIREWORKS: "fireworks_gpt_oss_120b",
}

BROWSER_ENDPOINT_KEYS = {
    LLMConfigForm.PROVIDER_OPENAI: "openai_gpt5_mini",
    LLMConfigForm.PROVIDER_OPENROUTER: "openrouter_glm_45",
    LLMConfigForm.PROVIDER_ANTHROPIC: "anthropic_sonnet4",
    LLMConfigForm.PROVIDER_FIREWORKS: "fireworks_qwen3_235b",
}

PROVIDER_KEY_MAP = {
    LLMConfigForm.PROVIDER_OPENAI: "openai",
    LLMConfigForm.PROVIDER_OPENROUTER: "openrouter",
    LLMConfigForm.PROVIDER_ANTHROPIC: "anthropic",
    LLMConfigForm.PROVIDER_FIREWORKS: "fireworks",
}

MODEL_PREFIXES = {
    LLMConfigForm.PROVIDER_OPENAI: "openai/",
    LLMConfigForm.PROVIDER_OPENROUTER: "openrouter/",
    LLMConfigForm.PROVIDER_ANTHROPIC: "anthropic/",
    LLMConfigForm.PROVIDER_FIREWORKS: "fireworks_ai/",
}


def _normalize_model_identifier(provider_choice: str | None, model: str) -> str:
    model = (model or "").strip()
    if not model or not provider_choice:
        return model
    prefix = MODEL_PREFIXES.get(provider_choice)
    if not prefix or model.startswith(prefix):
        return model
    return f"{prefix}{model}"


def _resolve_provider_api_key(provider_choice: str, provided_key: str | None) -> Optional[str]:
    if provided_key:
        return provided_key
    provider_key = PROVIDER_KEY_MAP.get(provider_choice)
    if not provider_key:
        return provided_key
    provider = LLMProvider.objects.filter(key=provider_key).first()
    if not provider:
        return provided_key
    if provider.api_key_encrypted:
        try:
            return SecretsEncryption.decrypt_value(provider.api_key_encrypted)
        except Exception:
            logger.debug("Unable to decrypt provider key for %s", provider_key, exc_info=True)
    if provider.env_var_name:
        return os.getenv(provider.env_var_name)
    return provided_key


def _build_test_params(provider_choice: str, model: str, api_key: str | None, api_base: str | None) -> dict:
    if provider_choice == LLMConfigForm.PROVIDER_CUSTOM and not api_key:
        raise ValueError("Enter an API key for the custom provider before testing.")
    resolved_key = _resolve_provider_api_key(provider_choice, api_key)
    if not resolved_key:
        raise ValueError("Provide an API key or configure environment variables for this provider.")

    params: dict = {
        "temperature": 0.1,
        "api_key": resolved_key,
        "max_tokens": 64,
        "timeout": 15,
    }
    if provider_choice == LLMConfigForm.PROVIDER_CUSTOM:
        if not api_base:
            raise ValueError("Custom providers require an API base URL.")
        params["api_base"] = api_base.strip()
    else:
        default_base = DEFAULT_PROVIDER_API_BASES.get(provider_choice)
        if api_base or default_base:
            params["api_base"] = (api_base or default_base or "").strip()
    if provider_choice == LLMConfigForm.PROVIDER_OPENROUTER:
        headers = get_attribution_headers()
        if headers:
            params["extra_headers"] = headers

    required_temp = get_required_temperature_for_model(model)
    if required_temp is not None:
        params["temperature"] = required_temp
    return params


class SetupWizardView(View):
    template_name = "setup/wizard.html"

    def get(self, request):
        try:
            if is_initial_setup_complete(force_refresh=True):
                return redirect("/")

            self._ensure_database_ready()

            superuser_form = SuperuserSetupForm()
            llm_form = LLMConfigForm(initial=self._default_llm_initial())
            return render(
                request,
                self.template_name,
                {
                    "superuser_form": superuser_form,
                    "llm_form": llm_form,
                },
            )
        except OperationalError as exc:
            logger.warning("Setup wizard DB connection failed: %s", exc)
            return self._render_db_error(request)

    def post(self, request):
        superuser_form = SuperuserSetupForm(request.POST)
        llm_form = LLMConfigForm(request.POST)

        try:
            self._ensure_database_ready()

            if not (superuser_form.is_valid() and llm_form.is_valid()):
                return render(
                    request,
                    self.template_name,
                    {
                        "superuser_form": superuser_form,
                        "llm_form": llm_form,
                    },
                    status=400,
                )

            with transaction.atomic():
                user = self._setup_superuser(superuser_form.cleaned_data)
                orchestrator_provider, orchestrator_endpoint = self._configure_orchestrator(llm_form.cleaned_data)
                browser_endpoint = self._configure_browser(llm_form.cleaned_data, orchestrator_provider, orchestrator_endpoint)
                invalidate_llm_bootstrap_cache()
                logger.info(
                    "First-run setup completed with orchestrator endpoint %s and browser endpoint %s",
                    orchestrator_endpoint.key if orchestrator_endpoint else "?",
                    browser_endpoint.key if browser_endpoint else "?",
                )
        except OperationalError as exc:
            logger.warning("Setup wizard DB connection failed during POST: %s", exc)
            return self._render_db_error(request, status=503)
        except Exception as exc:  # pragma: no cover - safety net for setup
            logger.exception("Setup wizard failed")
            messages.error(request, f"Setup failed: {exc}")
            return render(
                request,
                self.template_name,
                {
                    "superuser_form": superuser_form,
                    "llm_form": llm_form,
                },
                status=500,
            )

        # Auto-login the operator if possible
        self._attempt_login(request, user, superuser_form.cleaned_data["password1"])
        messages.success(request, "Setup complete! You're ready to start using Gobii.")
        return redirect("/")

    # ------------------------------------------------------------------
    # form defaults
    # ------------------------------------------------------------------
    def _default_llm_initial(self):
        default_provider = LLMConfigForm.PROVIDER_OPENROUTER
        return {
            "orchestrator_provider": default_provider,
            "orchestrator_model": DEFAULT_ORCHESTRATOR_MODELS.get(default_provider, ""),
            "orchestrator_supports_vision": False,
            "browser_same_as_orchestrator": True,
            "browser_model": DEFAULT_BROWSER_MODELS.get(default_provider, ""),
            "browser_supports_vision": False,
            "browser_provider": default_provider,
        }

    def _ensure_database_ready(self) -> None:
        """Best-effort connection check so we can show a human-friendly error."""
        try:
            connections["default"].ensure_connection()
        except OperationalError:
            raise

    def _render_db_error(self, request, *, status: int = 503):
        return render(
            request,
            "setup/db_error.html",
            {},
            status=status,
        )

    # ------------------------------------------------------------------
    # superuser setup
    # ------------------------------------------------------------------
    def _setup_superuser(self, data: dict):
        email = data["email"].strip().lower()
        password = data["password1"]
        User = get_user_model()
        username_field = User.USERNAME_FIELD
        lookup = {username_field: email}
        defaults = {
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        }
        if username_field != "email":
            defaults["email"] = email

        user, created = User.objects.get_or_create(defaults=defaults, **lookup)
        if not created:
            if username_field != "email":
                user.email = email
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
        user.set_password(password)
        user.save()
        return user

    def _attempt_login(self, request, user, password: str) -> None:
        if not user:
            return
        username_field = user.__class__.USERNAME_FIELD
        credentials = {username_field: getattr(user, username_field)}
        credentials["password"] = password
        authenticated = authenticate(request, **credentials)
        if authenticated is not None:
            try:
                login(request, authenticated)
            except Exception:
                logger.warning("Auto-login after setup failed", exc_info=True)

    # ------------------------------------------------------------------
    # LLM configuration helpers
    # ------------------------------------------------------------------
    def _configure_orchestrator(self, data: dict) -> Tuple[LLMProvider, PersistentModelEndpoint]:
        provider_choice: str = data["orchestrator_provider"]
        api_key: str = data.get("orchestrator_api_key", "")
        model: str = data.get("orchestrator_model", "").strip() or DEFAULT_ORCHESTRATOR_MODELS.get(provider_choice, "")
        api_base: str = data.get("orchestrator_api_base", "").strip()
        supports_tool_choice: bool = bool(data.get("orchestrator_supports_tool_choice"))
        use_parallel_tools: bool = bool(data.get("orchestrator_use_parallel_tools"))
        supports_vision: bool = bool(data.get("orchestrator_supports_vision"))
        if provider_choice != LLMConfigForm.PROVIDER_CUSTOM:
            model = _normalize_model_identifier(provider_choice, model)

        if provider_choice == LLMConfigForm.PROVIDER_CUSTOM:
            provider = self._create_custom_provider(
                display_name=data.get("orchestrator_custom_name") or "Custom LLM",
                base_slug="custom-orchestrator",
                api_key=api_key,
                browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
            )
            endpoint = self._create_or_update_persistent_endpoint(
                key_slug=f"{provider.key}-persistent",
                provider=provider,
                litellm_model=model,
                api_base=api_base,
                supports_tool_choice=supports_tool_choice,
                use_parallel_tools=use_parallel_tools,
                supports_vision=supports_vision,
            )
        else:
            provider_key = PROVIDER_KEY_MAP[provider_choice]
            provider = LLMProvider.objects.get(key=provider_key)
            if api_key:
                provider.api_key_encrypted = SecretsEncryption.encrypt_value(api_key)
            provider.enabled = True
            provider.save()

            endpoint_key = ORCHESTRATOR_ENDPOINT_KEYS[provider_choice]
            endpoint = PersistentModelEndpoint.objects.get(key=endpoint_key)
            if model:
                endpoint.litellm_model = model
            endpoint.temperature_override = get_required_temperature_for_model(endpoint.litellm_model)
            endpoint.supports_tool_choice = supports_tool_choice
            endpoint.use_parallel_tool_calls = use_parallel_tools
            endpoint.supports_vision = supports_vision
            endpoint.enabled = True
            if api_base:
                endpoint.api_base = api_base
            elif provider_choice == LLMConfigForm.PROVIDER_CUSTOM:
                endpoint.api_base = api_base
            else:
                if provider_choice in DEFAULT_BROWSER_BASE_URLS:
                    endpoint.api_base = DEFAULT_BROWSER_BASE_URLS.get(provider_choice, "")
            endpoint.save()

        self._reset_persistent_tiers(endpoint)
        return provider, endpoint

    def _configure_browser(
        self,
        data: dict,
        orchestrator_provider: LLMProvider,
        orchestrator_endpoint: PersistentModelEndpoint,
    ) -> BrowserModelEndpoint:
        same = data.get("browser_same_as_orchestrator")
        orchestrator_supports_vision = bool(data.get("orchestrator_supports_vision"))
        browser_supports_vision_raw = data.get("browser_supports_vision")
        browser_supports_vision = (
            bool(browser_supports_vision_raw)
            if browser_supports_vision_raw is not None
            else orchestrator_supports_vision
        )

        if same:
            provider = orchestrator_provider
            provider_choice = self._provider_choice_from_provider(provider)
            model = data.get("browser_model", "").strip() or DEFAULT_BROWSER_MODELS.get(provider_choice, "")
            if provider_choice and provider_choice != LLMConfigForm.PROVIDER_CUSTOM:
                model = _normalize_model_identifier(provider_choice, model)
            api_base = data.get("browser_api_base", "").strip()
            if not api_base:
                api_base = orchestrator_endpoint.api_base or DEFAULT_BROWSER_BASE_URLS.get(provider_choice, "")
            endpoint_key_hint = BROWSER_ENDPOINT_KEYS.get(provider_choice)
            endpoint = self._ensure_browser_endpoint(
                provider=provider,
                key_hint=endpoint_key_hint or f"{provider.key}-browser",
                model=model,
                api_base=api_base or DEFAULT_BROWSER_BASE_URLS.get(provider_choice, ""),
                supports_vision=browser_supports_vision,
            )
        else:
            provider_choice = data.get("browser_provider")
            api_key = data.get("browser_api_key", "")
            model = data.get("browser_model", "").strip() or DEFAULT_BROWSER_MODELS.get(provider_choice, "")
            if provider_choice and provider_choice != LLMConfigForm.PROVIDER_CUSTOM:
                model = _normalize_model_identifier(provider_choice, model)
            api_base = data.get("browser_api_base", "").strip()

            if provider_choice == LLMConfigForm.PROVIDER_CUSTOM:
                provider = self._create_custom_provider(
                    display_name=data.get("browser_custom_name") or "Custom Browser LLM",
                    base_slug="custom-browser",
                    api_key=api_key,
                    browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
                )
                endpoint = self._ensure_browser_endpoint(
                    provider=provider,
                    key_hint=f"{provider.key}-browser",
                    model=model,
                    api_base=api_base,
                    supports_vision=browser_supports_vision,
                )
            else:
                provider_key = PROVIDER_KEY_MAP[provider_choice]
                provider = LLMProvider.objects.get(key=provider_key)
                if api_key:
                    provider.api_key_encrypted = SecretsEncryption.encrypt_value(api_key)
                provider.enabled = True
                provider.save()

                endpoint_key = BROWSER_ENDPOINT_KEYS[provider_choice]
                endpoint = self._ensure_browser_endpoint(
                    provider=provider,
                    key_hint=endpoint_key,
                    model=model,
                    api_base=api_base or DEFAULT_BROWSER_BASE_URLS.get(provider_choice, ""),
                    supports_vision=browser_supports_vision,
                )

        self._reset_browser_policy(endpoint)
        return endpoint

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _create_custom_provider(
        self,
        display_name: str,
        base_slug: str,
        api_key: str,
        browser_backend: str,
    ) -> LLMProvider:
        slug = slugify(display_name) or slugify(base_slug) or f"custom-{secrets.token_hex(2)}"
        original_slug = slug
        counter = 1
        while LLMProvider.objects.filter(key=slug).exists():
            slug = f"{original_slug}-{counter}"
            counter += 1

        provider, _ = LLMProvider.objects.get_or_create(
            key=slug,
            defaults={
                "display_name": display_name,
                "browser_backend": browser_backend,
                "enabled": True,
            },
        )
        provider.display_name = display_name
        provider.browser_backend = browser_backend
        provider.enabled = True
        if api_key:
            provider.api_key_encrypted = SecretsEncryption.encrypt_value(api_key)
        provider.save()
        return provider

    def _create_or_update_persistent_endpoint(
        self,
        key_slug: str,
        provider: LLMProvider,
        litellm_model: str,
        api_base: str,
        supports_tool_choice: bool,
        use_parallel_tools: bool,
        supports_vision: bool,
    ) -> PersistentModelEndpoint:
        endpoint, _ = PersistentModelEndpoint.objects.get_or_create(
            key=slugify(key_slug)[:96],
            defaults={
                "provider": provider,
                "litellm_model": litellm_model,
            },
        )
        endpoint.provider = provider
        endpoint.litellm_model = litellm_model
        endpoint.temperature_override = get_required_temperature_for_model(litellm_model)
        endpoint.api_base = api_base
        endpoint.supports_tool_choice = supports_tool_choice
        endpoint.use_parallel_tool_calls = use_parallel_tools
        endpoint.supports_vision = supports_vision
        endpoint.enabled = True
        endpoint.save()
        return endpoint

    def _reset_persistent_tiers(self, endpoint: PersistentModelEndpoint) -> None:
        if not PersistentTokenRange.objects.exists():
            default_range = PersistentTokenRange.objects.create(name="default", min_tokens=0, max_tokens=None)
            PersistentLLMTier.objects.create(token_range=default_range, order=1, description="Primary")
        if not PersistentLLMTier.objects.exists():
            token_range = PersistentTokenRange.objects.first()
            PersistentLLMTier.objects.create(token_range=token_range, order=1, description="Primary")

        PersistentTierEndpoint.objects.all().delete()
        for tier in PersistentLLMTier.objects.all():
            PersistentTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=1.0)

        # Disable other endpoints to avoid accidental selection without keys
        PersistentModelEndpoint.objects.exclude(pk=endpoint.pk).update(enabled=False)

    def _ensure_browser_endpoint(
        self,
        provider: LLMProvider,
        key_hint: str,
        model: str,
        api_base: str,
        supports_vision: bool,
    ) -> BrowserModelEndpoint:
        key = slugify(key_hint)[:96]
        endpoint, _ = BrowserModelEndpoint.objects.get_or_create(
            key=key,
            defaults={
                "provider": provider,
                "browser_model": model,
                "browser_base_url": api_base,
            },
        )
        endpoint.provider = provider
        endpoint.browser_model = model
        endpoint.browser_base_url = api_base
        endpoint.supports_vision = supports_vision
        endpoint.enabled = True
        endpoint.save()
        BrowserModelEndpoint.objects.exclude(pk=endpoint.pk).update(enabled=False)
        return endpoint

    def _reset_browser_policy(self, endpoint: BrowserModelEndpoint) -> None:
        policy, _ = BrowserLLMPolicy.objects.get_or_create(name="Default", defaults={"is_active": True})
        policy.is_active = True
        policy.save()
        policy.tiers.all().delete()
        tier = BrowserLLMTier.objects.create(policy=policy, order=1, description="Primary")
        BrowserTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=1.0)

    def _provider_choice_from_provider(self, provider: LLMProvider) -> Optional[str]:
        reverse_map = {v: k for k, v in PROVIDER_KEY_MAP.items()}
        return reverse_map.get(provider.key)


def setup_complete_view(request):
    """Redirect helper once setup is complete."""
    if not is_initial_setup_complete(force_refresh=True):
        return redirect(reverse("setup:wizard"))
    return redirect("/")


@require_POST
def test_llm_connection(request):
    """Ad-hoc LiteLLM connectivity test for the setup wizard."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "message": "Invalid payload."}, status=400)

    target = payload.get("target") or "orchestrator"
    provider_choice = payload.get("provider")
    model = (payload.get("model") or "").strip()
    api_key = payload.get("api_key")
    api_base = (payload.get("api_base") or "").strip() or None

    if not provider_choice or not model:
        return JsonResponse({"ok": False, "message": "Select a provider and enter a model name before testing."}, status=400)

    if provider_choice != LLMConfigForm.PROVIDER_CUSTOM:
        model = _normalize_model_identifier(provider_choice, model)

    try:
        params = _build_test_params(provider_choice, model, api_key, api_base)
    except ValueError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    messages = [
        {"role": "system", "content": "You are a connectivity probe. Reply briefly."},
        {"role": "user", "content": "Say READY."},
    ]

    try:
        response = run_completion(model=model, messages=messages, params=params, drop_params=True)
    except Exception as exc:
        logger.warning("LLM test failed for %s provider %s: %s", target, provider_choice, exc, exc_info=True)
        return JsonResponse(
            {
                "ok": False,
                "message": f"{type(exc).__name__}: {exc}",
            },
            status=400,
        )

    preview = ""
    if getattr(response, "choices", None):
        preview = getattr(response.choices[0].message, "content", "") or ""
    usage = getattr(response, "model_extra", {}).get("usage") if hasattr(response, "model_extra") else None
    total_tokens = getattr(usage, "total_tokens", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)

    return JsonResponse(
        {
            "ok": True,
            "message": "LLM responded successfully.",
            "model": model,
            "provider": provider_choice,
            "preview": (preview or "").strip()[:200],
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
    )
