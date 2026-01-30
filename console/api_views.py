import json
import logging
import mimetypes
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import zstandard as zstd
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Min, Max, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.urls import NoReverseMatch, reverse
from django.utils.text import get_valid_filename

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.files.attachment_helpers import load_signed_filespace_download_payload
from api.agent.files.filespace_service import dedupe_name, get_or_create_default_filespace
from api.agent.tools.mcp_manager import get_mcp_manager
from api.models import (
    BrowserLLMPolicy,
    BrowserUseAgent,
    BrowserLLMTier,
    BrowserModelEndpoint,
    BrowserTierEndpoint,
    CommsChannel,
    EmbeddingsLLMTier,
    EmbeddingsModelEndpoint,
    EmbeddingsTierEndpoint,
    FileHandlerLLMTier,
    FileHandlerModelEndpoint,
    FileHandlerTierEndpoint,
    IntelligenceTier,
    LLMProvider,
    MCPServerConfig,
    MCPServerOAuthCredential,
    MCPServerOAuthSession,
    AgentEmailAccount,
    AgentEmailOAuthCredential,
    AgentEmailOAuthSession,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentSystemMessage,
    PersistentLLMTier,
    PersistentModelEndpoint,
    PersistentTierEndpoint,
    PersistentTokenRange,
    EvalSuiteRun,
    EvalRun,
    EvalRunTask,
    PersistentAgentPromptArchive,
    AgentFileSpaceAccess,
    AgentFsNode,
    Organization,
    OrganizationMembership,
    AgentCollaborator,
    build_web_agent_address,
    build_web_user_address,
    UserPhoneNumber,
)
from django.core.files.storage import default_storage
from agents.services import PretrainedWorkerTemplateService
from config.socialaccount_adapter import OAUTH_CHARTER_COOKIE
from console.agent_audit.events import fetch_audit_events, fetch_audit_events_between
from console.agent_audit.timeline import build_audit_timeline
from console.agent_audit.serializers import serialize_system_message
from console.agent_chat.timeline import compute_processing_status
from api.encryption import SecretsEncryption
from api.agent.tasks import process_agent_events_task
from api.services.web_sessions import (
    WEB_SESSION_TTL_SECONDS,
    end_web_session,
    heartbeat_web_session,
    start_web_session,
    touch_web_session,
)

from util import sms
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

from console.agent_chat.access import (
    agent_queryset_for,
    resolve_agent_for_request,
    shared_agent_queryset_for,
    user_can_manage_agent,
    user_is_collaborator,
)
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE,
    TimelineDirection,
    build_processing_snapshot,
    compute_processing_status,
    fetch_timeline_window,
    serialize_message_event,
    serialize_processing_snapshot,
)
from console.context_helpers import build_console_context, resolve_console_context
from console.context_overrides import get_context_override
from console.forms import MCPServerConfigForm, PhoneAddForm, PhoneVerifyForm
from console.phone_utils import get_phone_cooldown_remaining, get_primary_phone, serialize_phone
from console.agent_quick_settings import build_agent_quick_settings_payload
from console.views import build_llm_intelligence_props
from console.agent_addons import (
    build_agent_addons_payload,
    update_contact_pack_quantities,
    update_task_pack_quantities,
)
from console.daily_credit import (
    build_agent_daily_credit_context,
    build_daily_credit_status,
    parse_daily_credit_limit,
    serialize_daily_credit_payload,
)
from console.agent_creation import enable_agent_sms_contact
from console.agent_reassignment import reassign_agent_organization
from console.views import _track_org_event_for_console, _mcp_server_event_properties
from api.services.sandbox_compute import SANDBOX_COMPUTE_WAFFLE_FLAG
from waffle import flag_is_active
from console.llm_serializers import build_llm_overview
import litellm

from api.agent.core.llm_config import invalidate_llm_bootstrap_cache
from api.agent.core.llm_utils import run_completion
from api.evals.tasks import gc_eval_runs_task
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry
from api.evals.tasks import run_eval_task
from api.evals.runner import _update_suite_state
from api.evals.realtime import broadcast_run_update, broadcast_suite_update
from api.llm.utils import normalize_model_name
from api.openrouter import DEFAULT_API_BASE, get_attribution_headers
from api.services import mcp_servers as mcp_server_service
from api.services.template_clone import TemplateCloneError, TemplateCloneService
from api.services.daily_credit_limits import get_agent_credit_multiplier
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from constants.plans import PlanNamesChoices
from util.integrations import stripe_status
from util.subscription_helper import get_active_subscription, get_organization_plan, get_user_plan


logger = logging.getLogger(__name__)

GOOGLE_PROVIDER_KEYS = {"gmail", "google"}
MICROSOFT_PROVIDER_KEYS = {"outlook", "o365", "office365", "microsoft"}
MANAGED_EMAIL_PROVIDER_KEYS = GOOGLE_PROVIDER_KEYS | MICROSOFT_PROVIDER_KEYS

BILLING_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.BILLING,
}


def _can_manage_contact_packs(request: HttpRequest, agent: PersistentAgent, plan_payload: dict | None) -> bool:
    if not stripe_status().enabled:
        return False
    plan_id = str((plan_payload or {}).get("id") or "").lower()
    if not plan_id or plan_id == PlanNamesChoices.FREE.value:
        return False

    if not agent.organization_id:
        return True

    owner = agent.organization or agent.user
    subscription = get_active_subscription(owner, preferred_plan_id=(plan_payload or {}).get("id"))
    if not subscription:
        return False

    if agent.organization_id:
        membership = OrganizationMembership.objects.filter(
            user=request.user,
            org=agent.organization,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).first()
        if not membership or membership.role not in BILLING_MANAGE_ROLES:
            return False
    return True


class ApiLoginRequiredMixin(LoginRequiredMixin):
    """Return JSON 401 instead of redirecting to the login page."""

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required"}, status=401)
        return super().handle_no_permission()


class ConsoleSessionAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse(
            {
                "user_id": str(request.user.id),
                "email": request.user.email,
            }
        )


class AgentSpawnIntentAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        restored_cookie = False
        if "agent_charter" not in request.session:
            cookie_value = request.COOKIES.get(OAUTH_CHARTER_COOKIE)
            if cookie_value:
                try:
                    stashed = signing.loads(cookie_value, max_age=3600)
                    for key in (
                        "agent_charter",
                        PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY,
                        "agent_charter_source",
                    ):
                        if key in stashed:
                            request.session[key] = stashed[key]
                    request.session.modified = True
                    restored_cookie = True
                except (signing.BadSignature, signing.SignatureExpired):
                    logger.debug("Invalid or expired OAuth charter cookie")

        payload = {
            "charter": request.session.get("agent_charter"),
            "preferred_llm_tier": request.session.get("agent_preferred_llm_tier"),
        }
        response = JsonResponse(payload)
        if restored_cookie:
            response.delete_cookie(OAUTH_CHARTER_COOKIE)
        return response


def _path_meta(path: str | None) -> tuple[str | None, str | None]:
    if not path:
        return None, None
    parent = path.rsplit("/", 1)[0] or "/"
    return parent, None


def _resolve_agent_email_account(request: HttpRequest, account_id: str) -> AgentEmailAccount:
    return get_object_or_404(
        AgentEmailAccount.objects.select_related("endpoint__owner_agent"),
        pk=account_id,
        endpoint__owner_agent__user=request.user,
    )


def _resolve_managed_email_oauth_client(provider: str) -> tuple[str, str]:
    provider_key = provider.lower()
    if provider_key in GOOGLE_PROVIDER_KEYS:
        return (
            os.getenv("GOOGLE_CLIENT_ID", ""),
            os.getenv("GOOGLE_CLIENT_SECRET", ""),
        )
    if provider_key in MICROSOFT_PROVIDER_KEYS:
        return (
            os.getenv("MICROSOFT_CLIENT_ID", ""),
            os.getenv("MICROSOFT_CLIENT_SECRET", ""),
        )
    return "", ""


def _ext_from_name(name: str | None) -> str | None:
    if not name or "." not in name:
        return None
    return name.rsplit(".", 1)[-1].lower() or None


def _ensure_console_endpoints(agent: PersistentAgent, user) -> tuple[str, str]:
    """Ensure dedicated console endpoints exist and return (sender, recipient) addresses."""
    channel = CommsChannel.WEB
    sender_address = build_web_user_address(user.id, agent.id)
    recipient_address = build_web_agent_address(agent.id)

    agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=recipient_address,
        defaults={
            "owner_agent": agent,
            "is_primary": bool(
                agent.preferred_contact_endpoint
                and agent.preferred_contact_endpoint.channel == CommsChannel.WEB
            ),
        },
    )
    updates = []
    if agent_endpoint.owner_agent_id != agent.id:
        agent_endpoint.owner_agent = agent
        updates.append("owner_agent")
    if not agent_endpoint.address:
        agent_endpoint.address = recipient_address
        updates.append("address")
    if updates:
        agent_endpoint.save(update_fields=updates)

    PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=sender_address,
        defaults={"owner_agent": None, "is_primary": False},
    )
    return sender_address, recipient_address


_TEST_COMPLETION_MESSAGES = [
    {"role": "system", "content": "You are a connectivity probe. Reply briefly."},
    {"role": "user", "content": "Respond with the word READY."},
]

_TEST_EMBEDDING_INPUT = "Connectivity test for embeddings."


def _resolve_provider_api_key(provider: LLMProvider | None) -> str | None:
    if provider is None or not provider.enabled:
        return None
    if provider.api_key_encrypted:
        try:
            return SecretsEncryption.decrypt_value(provider.api_key_encrypted)
        except Exception:
            logger.warning("Failed to decrypt API key for provider %s", provider.key, exc_info=True)
    if provider.env_var_name:
        env_value = os.getenv(provider.env_var_name)
        if env_value:
            return env_value
    return None


def _apply_provider_overrides(provider: LLMProvider | None, params: dict[str, Any]) -> None:
    if provider is None:
        return
    if provider.key == "google":
        project = provider.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
        location = provider.vertex_location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")
        params["vertex_project"] = project
        params["vertex_location"] = location
    if provider.key == "openrouter":
        headers = get_attribution_headers()
        if headers:
            params["extra_headers"] = headers


def _build_completion_params(
    endpoint,
    provider: LLMProvider | None,
    *,
    model_attr: str,
    base_attr: str,
    default_temperature: float = 0.1,
    default_max_tokens: int = 96,
) -> tuple[str, dict[str, Any]]:
    if not getattr(endpoint, "enabled", False):
        raise ValueError("Endpoint is disabled")
    if provider is None:
        raise ValueError("Endpoint is missing a linked provider")
    if not provider.enabled:
        raise ValueError("Provider is disabled")

    raw_model = (getattr(endpoint, model_attr, "") or "").strip()
    if not raw_model:
        raise ValueError("Endpoint does not specify a model identifier")
    api_base = (getattr(endpoint, base_attr, "") or "").strip() or None
    model = normalize_model_name(provider, raw_model, api_base=api_base)

    supports_temperature = bool(getattr(endpoint, "supports_temperature", True))
    temperature: float | None = None
    if supports_temperature:
        temp_override = getattr(endpoint, "temperature_override", None)
        temperature = float(temp_override if temp_override not in (None, "") else default_temperature)
    max_tokens_value = getattr(endpoint, "max_output_tokens", None)
    max_tokens = default_max_tokens
    if isinstance(max_tokens_value, (int, float)) and max_tokens_value > 0:
        max_tokens = min(int(max_tokens_value), 512)

    params: dict[str, Any] = {
        "max_tokens": max_tokens,
        "timeout": 20,
    }
    if temperature is not None:
        params["temperature"] = temperature
    params["supports_temperature"] = supports_temperature
    if hasattr(endpoint, "supports_tool_choice"):
        params["supports_tool_choice"] = bool(getattr(endpoint, "supports_tool_choice", True))
    if hasattr(endpoint, "use_parallel_tool_calls"):
        params["use_parallel_tool_calls"] = bool(getattr(endpoint, "use_parallel_tool_calls", True))
    if hasattr(endpoint, "supports_vision"):
        params["supports_vision"] = bool(getattr(endpoint, "supports_vision", False))
    if hasattr(endpoint, "supports_reasoning"):
        supports_reasoning = bool(getattr(endpoint, "supports_reasoning", False))
        params["supports_reasoning"] = supports_reasoning
        if supports_reasoning:
            effort = getattr(endpoint, "reasoning_effort", None)
            if effort:
                params["reasoning_effort"] = effort
    if provider.key == "openrouter":
        openrouter_preset = (getattr(endpoint, "openrouter_preset", "") or "").strip()
        if openrouter_preset:
            params["preset"] = openrouter_preset

    if api_base:
        params["api_base"] = api_base

    api_key = _resolve_provider_api_key(provider)
    is_openai_compat = model.startswith("openai/") and api_base
    if not api_key and is_openai_compat:
        api_key = "sk-noauth"
    if not api_key:
        raise ValueError("Configure an API key or environment variable for this provider before testing")
    params["api_key"] = api_key

    _apply_provider_overrides(provider, params)
    return model, params


def _extract_completion_usage(response: Any) -> dict[str, Any]:
    model_extra = getattr(response, "model_extra", None)
    if isinstance(model_extra, dict):
        usage = model_extra.get("usage")
    else:
        usage = getattr(model_extra, "usage", None)
    if usage is None:
        usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    return {
        "total_tokens": getattr(usage, "total_tokens", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def _extract_completion_preview(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return (content or "").strip()


def _run_completion_test(endpoint, provider: LLMProvider, *, model_attr: str, base_attr: str, default_max_tokens: int) -> dict[str, Any]:
    model, params = _build_completion_params(
        endpoint,
        provider,
        model_attr=model_attr,
        base_attr=base_attr,
        default_max_tokens=default_max_tokens,
    )
    started = time.monotonic()
    response = run_completion(model=model, messages=_TEST_COMPLETION_MESSAGES, params=params, drop_params=True)
    latency_ms = int((time.monotonic() - started) * 1000)
    preview = _extract_completion_preview(response)
    usage = _extract_completion_usage(response)
    return {
        "message": "Endpoint responded successfully.",
        "model": model,
        "provider": provider.display_name,
        "preview": preview,
        "latency_ms": latency_ms,
        "total_tokens": usage.get("total_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def _extract_embedding_dimension(response: Any) -> int | None:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not data:
        return None
    first = data[0]
    embedding = getattr(first, "embedding", None)
    if embedding is None and isinstance(first, dict):
        embedding = first.get("embedding")
    if embedding is None:
        return None
    try:
        return len(list(embedding))
    except TypeError:
        return None


def _run_embedding_test(endpoint: EmbeddingsModelEndpoint) -> dict[str, Any]:
    if not endpoint.enabled:
        raise ValueError("Endpoint is disabled")
    provider = endpoint.provider
    if provider and not provider.enabled:
        raise ValueError("Provider is disabled")
    raw_model = (endpoint.litellm_model or "").strip()
    api_base = (endpoint.api_base or "").strip() or None
    model = normalize_model_name(provider, raw_model, api_base=api_base)
    if not model:
        raise ValueError("Endpoint does not specify a model identifier")
    api_key = _resolve_provider_api_key(provider)
    if not api_key and api_base:
        api_key = "sk-noauth"
    if not api_key:
        raise ValueError("Configure an API key or environment variable for this provider before testing")
    params: dict[str, Any] = {"api_key": api_key}
    if api_base:
        params["api_base"] = api_base
    _apply_provider_overrides(provider, params)

    started = time.monotonic()
    response = litellm.embedding(model=model, input=[_TEST_EMBEDDING_INPUT], **params)
    latency_ms = int((time.monotonic() - started) * 1000)
    dimension = _extract_embedding_dimension(response)
    return {
        "message": "Embedding generated successfully.",
        "model": model,
        "provider": provider.display_name if provider else "Unlinked",
        "dimensions": dimension,
        "latency_ms": latency_ms,
    }


def _resolve_mcp_server_config(request: HttpRequest, config_id: str) -> MCPServerConfig:
    """Resolve an MCP server configuration the user is allowed to manage."""
    config = get_object_or_404(MCPServerConfig, pk=config_id)
    if config.scope == MCPServerConfig.Scope.PLATFORM:
        raise PermissionDenied("Platform-managed MCP servers cannot be modified from the console.")

    if config.scope == MCPServerConfig.Scope.USER:
        if config.user_id != request.user.id:
            raise PermissionDenied("You do not have access to this MCP server.")
    elif config.scope == MCPServerConfig.Scope.ORGANIZATION:
        context = build_console_context(request)
        membership = context.current_membership
        if (
            context.current_context.type != "organization"
            or membership is None
            or str(membership.org_id) != str(config.organization_id)
            or not context.can_manage_org_agents
        ):
            raise PermissionDenied("You do not have access to this MCP server.")
    return config


def _require_active_session(request: HttpRequest, session_id: uuid.UUID) -> MCPServerOAuthSession:
    """Fetch a pending OAuth session and enforce ownership + expiry."""
    session = get_object_or_404(MCPServerOAuthSession, pk=session_id)

    if session.initiated_by_id != request.user.id:
        raise PermissionDenied("You do not have access to this OAuth session.")

    if session.has_expired():
        session.delete()
        raise PermissionDenied("OAuth session has expired. Restart the flow.")

    # Re-check access against server configuration in case ownership changed mid-flow.
    _resolve_mcp_server_config(request, str(session.server_config_id))
    return session


def _require_active_email_oauth_session(request: HttpRequest, session_id: uuid.UUID) -> AgentEmailOAuthSession:
    """Fetch a pending email OAuth session and enforce ownership + expiry."""
    session = get_object_or_404(AgentEmailOAuthSession, pk=session_id)
    if session.initiated_by_id != request.user.id:
        raise PermissionDenied("You do not have access to this OAuth session.")
    if session.expires_at <= timezone.now():
        session.delete()
        raise PermissionDenied("OAuth session has expired. Restart the flow.")
    _resolve_agent_email_account(request, str(session.account_id))
    return session


def _resolve_mcp_owner(request: HttpRequest) -> tuple[str, str, object | None, object | None]:
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization MCP servers.")
        return (
            "organization",
            membership.org.name,
            None,
            membership.org,
        )

    label = request.user.get_full_name() or request.user.username or request.user.email or "Personal"
    return ("user", label, request.user, None)


def _owner_queryset(owner_scope: str, owner_user, owner_org):
    queryset = MCPServerConfig.objects.select_related("oauth_credential")
    if owner_scope == "organization" and owner_org is not None:
        return queryset.filter(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=owner_org,
        ).order_by("display_name")
    return queryset.filter(
        scope=MCPServerConfig.Scope.USER,
        user=owner_user,
    ).order_by("display_name")


def _serialize_mcp_server(
    server: MCPServerConfig,
    request: HttpRequest | None = None,
    pending_servers: set[str] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": str(server.id),
        "name": server.name,
        "display_name": server.display_name,
        "description": server.description,
        "command": server.command,
        "command_args": server.command_args,
        "url": server.url,
        "auth_method": server.auth_method,
        "is_active": server.is_active,
        "scope": server.scope,
        "scope_label": server.get_scope_display(),
        "updated_at": server.updated_at.isoformat(),
        "created_at": server.created_at.isoformat(),
    }
    if request is not None:
        pending = False
        if (
            request.user.is_authenticated
            and server.auth_method == MCPServerConfig.AuthMethod.OAUTH2
        ):
            if pending_servers is not None:
                pending = str(server.id) in pending_servers
            else:
                pending = server.oauth_sessions.filter(
                    initiated_by=request.user,
                    expires_at__gt=timezone.now(),
                ).exists()
        credential = getattr(server, "oauth_credential", None)
        if credential is None:
            try:
                credential = server.oauth_credential
            except MCPServerOAuthCredential.DoesNotExist:
                credential = None
        data.update(
            {
                "oauth_status_url": reverse("console-mcp-oauth-status", args=[server.id]),
                "oauth_revoke_url": reverse("console-mcp-oauth-revoke", args=[server.id]),
                "oauth_connected": credential is not None,
                "oauth_pending": pending,
            }
        )
    return data


def _serialize_mcp_server_detail(server: MCPServerConfig, request: HttpRequest | None = None) -> dict[str, object]:
    data = _serialize_mcp_server(server, request=request)
    data.update(
        {
            "metadata": server.metadata or {},
            "headers": server.headers or {},
            "environment": server.environment or {},
            "prefetch_apps": server.prefetch_apps or [],
            "command": server.command,
            "command_args": server.command_args or [],
            "description": server.description,
        }
    )
    if request is not None:
        data["oauth_status_url"] = reverse("console-mcp-oauth-status", args=[server.id])
        data["oauth_revoke_url"] = reverse("console-mcp-oauth-revoke", args=[server.id])
    return data


def _form_errors(form: MCPServerConfigForm) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    for field, field_errors in form.errors.items():
        errors[field] = [str(error) for error in field_errors]
    non_field = form.non_field_errors()
    if non_field:
        errors["non_field_errors"] = [str(error) for error in non_field]
    return errors


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    return payload


def _json_ok(**extra):
    payload = {"ok": True}
    payload.update(extra)
    return JsonResponse(payload)


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


_REASONING_EFFORT_VALUES = set(PersistentModelEndpoint.ReasoningEffort.values)


def _coerce_reasoning_effort(value) -> str | None:
    if value in (None, ""):
        return None
    effort = str(value).strip().lower()
    if effort not in _REASONING_EFFORT_VALUES:
        allowed = ", ".join(sorted(_REASONING_EFFORT_VALUES))
        raise ValueError(f"reasoning_effort must be one of: {allowed}")
    return effort


def _validate_reasoning_override(endpoint, value) -> str | None:
    reasoning_override = _coerce_reasoning_effort(value)
    if reasoning_override and not getattr(endpoint, "supports_reasoning", False):
        raise ValueError("Endpoint does not support reasoning; cannot set reasoning_effort_override")
    return reasoning_override


def _resolve_intelligence_tier_from_payload(payload) -> "IntelligenceTier":
    tier_key = (payload.get("intelligence_tier") or "").strip()
    if not tier_key:
        is_premium = _coerce_bool(payload.get("is_premium", False))
        is_max = _coerce_bool(payload.get("is_max", False))
        if is_premium and is_max:
            raise ValueError("Tier cannot be both premium and max.")
        if is_max:
            tier_key = "max"
        elif is_premium:
            tier_key = "premium"
        else:
            tier_key = "standard"
    tier = IntelligenceTier.objects.filter(key=tier_key).first()
    if tier is None:
        raise ValueError("Unsupported intelligence tier selection.")
    return tier


def _next_order_for_range(token_range: PersistentTokenRange, intelligence_tier: "IntelligenceTier") -> int:
    last = (
        PersistentLLMTier.objects.filter(
            token_range=token_range,
            intelligence_tier=intelligence_tier,
        )
        .order_by("-order")
        .first()
    )
    return (last.order if last else 0) + 1


def _next_order_for_browser(policy: BrowserLLMPolicy, intelligence_tier: "IntelligenceTier") -> int:
    last = (
        BrowserLLMTier.objects.filter(policy=policy, intelligence_tier=intelligence_tier)
        .order_by("-order")
        .first()
    )
    return (last.order if last else 0) + 1


def _next_embedding_order() -> int:
    last = EmbeddingsLLMTier.objects.order_by("-order").first()
    return (last.order if last else 0) + 1


def _next_file_handler_order() -> int:
    last = FileHandlerLLMTier.objects.order_by("-order").first()
    return (last.order if last else 0) + 1


def _swap_orders(queryset, item, direction: str) -> bool:
    siblings = list(queryset.order_by("order"))
    try:
        index = next(i for i, sibling in enumerate(siblings) if sibling.pk == item.pk)
    except StopIteration:
        return False
    if direction == "up" and index == 0:
        return False
    if direction == "down" and index == len(siblings) - 1:
        return False
    target_index = index - 1 if direction == "up" else index + 1
    other = siblings[target_index]
    model = queryset.model
    max_order = queryset.aggregate(max_order=Max("order")).get("max_order")
    sentinel = (max_order if max_order is not None else 0) + 1  # keep within PositiveIntegerField constraint
    original_item_order = item.order
    original_other_order = other.order
    new_item_order = original_other_order
    new_other_order = original_item_order
    original_item_description = (item.description or "").strip() if hasattr(item, "description") else ""
    original_other_description = (other.description or "").strip() if hasattr(other, "description") else ""

    def _should_reset_description(description: str, previous_order: int) -> bool:
        if not description:
            return True
        return description == f"Tier {previous_order}"

    def _should_reset_to_next(description: str, new_order: int) -> bool:
        if not description:
            return True
        return description == f"Tier {new_order}"

    with transaction.atomic():
        model.objects.filter(pk=item.pk).update(order=sentinel)
        model.objects.filter(pk=other.pk).update(order=original_item_order)
        model.objects.filter(pk=item.pk).update(order=original_other_order)
        if model is PersistentLLMTier:
            if _should_reset_description(original_item_description, original_item_order) or _should_reset_to_next(original_item_description, new_item_order):
                model.objects.filter(pk=item.pk).update(description=f"Tier {new_item_order}")
            if _should_reset_description(original_other_description, original_other_order) or _should_reset_to_next(original_other_description, new_other_order):
                model.objects.filter(pk=other.pk).update(description=f"Tier {new_other_order}")
    item.order, other.order = other.order, item.order
    if isinstance(item, PersistentLLMTier) and (_should_reset_description(original_item_description, original_item_order) or _should_reset_to_next(original_item_description, new_item_order)):
        item.description = f"Tier {new_item_order}"
    if isinstance(other, PersistentLLMTier) and (_should_reset_description(original_other_description, original_other_order) or _should_reset_to_next(original_other_description, new_other_order)):
        other.description = f"Tier {new_other_order}"
    return True


def _get_active_browser_policy() -> BrowserLLMPolicy:
    policy = BrowserLLMPolicy.objects.filter(is_active=True).first()
    if policy is None:
        policy = BrowserLLMPolicy.objects.create(name="Default", is_active=True)
    return policy


class SystemAdminAPIView(LoginRequiredMixin, View):
    """JSON API view restricted to staff/system administrators."""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if not (request.user.is_staff or request.user.is_superuser):
            return JsonResponse({"error": "forbidden"}, status=403)
        return super().dispatch(request, *args, **kwargs)


class StaffAgentSearchAPIView(SystemAdminAPIView):
    """Search persistent agents by name or id for the staff audit UI."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        query = (request.GET.get("q") or "").strip()
        limit_raw = request.GET.get("limit") or "8"
        try:
            limit = int(limit_raw)
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")
        limit = max(1, min(limit, 25))
        if not query:
            return JsonResponse({"agents": []})

        filters = Q(name__icontains=query)
        try:
            filters |= Q(id=uuid.UUID(query))
        except (TypeError, ValueError):
            pass

        matches = (
            PersistentAgent.objects.filter(filters)
            .only("id", "name")
            .order_by("name")[:limit]
        )
        payload = [{"id": str(agent.id), "name": agent.name or ""} for agent in matches]
        return JsonResponse({"agents": payload})


class StaffAgentAuditAPIView(SystemAdminAPIView):
    """Return audit runs (PROCESS_EVENTS loops) for any agent."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        cursor = request.GET.get("cursor") or None
        at_raw = request.GET.get("at")
        day_raw = request.GET.get("day")
        tz_offset_raw = request.GET.get("tz_offset_minutes")
        tz_offset = 0
        try:
            if tz_offset_raw is not None:
                tz_offset = int(tz_offset_raw)
        except ValueError:
            return HttpResponseBadRequest("tz_offset_minutes must be an integer")
        tzinfo = dt_timezone(timedelta(minutes=tz_offset))
        at_dt = None
        if at_raw:
            try:
                at_dt = datetime.fromisoformat(at_raw.replace("Z", "+00:00"))
                if timezone.is_naive(at_dt):
                    at_dt = timezone.make_aware(at_dt, timezone.get_current_timezone())
            except ValueError:
                return HttpResponseBadRequest("at must be an ISO8601 datetime")
        elif day_raw:
            try:
                day_dt = datetime.fromisoformat(day_raw).date()
            except ValueError:
                return HttpResponseBadRequest("day must be YYYY-MM-DD")
            at_dt = datetime.combine(day_dt + timedelta(days=1), datetime.min.time(), tzinfo=tzinfo)

        try:
            limit = int(request.GET.get("limit", 3))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")
        limit = max(1, min(limit, 50))

        events, has_more, next_cursor = fetch_audit_events(agent, cursor=cursor, limit=limit, at=at_dt)

        processing_active = compute_processing_status(agent)

        return JsonResponse(
            {
                "events": events,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "processing_active": processing_active,
                "agent": {
                    "id": str(agent.id),
                    "name": agent.name,
                    "color": agent.get_display_color(),
                },
            }
        )


class StaffAgentAuditTimelineAPIView(SystemAdminAPIView):
    """Return coarse activity buckets to drive audit timeline UI."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        days_raw = request.GET.get("days")
        days = None
        if days_raw:
            try:
                days = int(days_raw)
            except ValueError:
                return HttpResponseBadRequest("days must be an integer")
            days = max(1, min(days, 365))
        tz_offset_raw = request.GET.get("tz_offset_minutes")
        tz_offset = 0
        try:
            if tz_offset_raw is not None:
                tz_offset = int(tz_offset_raw)
        except ValueError:
            return HttpResponseBadRequest("tz_offset_minutes must be an integer")
        tzinfo = dt_timezone(timedelta(minutes=tz_offset))

        timeline = build_audit_timeline(agent, days=days, tzinfo=tzinfo)
        payload = {
            "buckets": [
                {
                    "day": bucket.day.isoformat(),
                    "count": bucket.count,
                }
                for bucket in timeline.buckets
            ],
            "latest": timeline.latest_day.isoformat() if timeline.latest_day else None,
            "days": timeline.span_days,
        }
        return JsonResponse(payload)


class StaffAgentAuditDayDebugAPIView(SystemAdminAPIView):
    """Temporary debug: return all audit events for a specific day."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        day_str = request.GET.get("day")
        if not day_str:
            return HttpResponseBadRequest("day is required (YYYY-MM-DD)")
        try:
            target_date = datetime.fromisoformat(day_str).date()
        except ValueError:
            return HttpResponseBadRequest("day must be YYYY-MM-DD")

        tz_offset_raw = request.GET.get("tz_offset_minutes")
        tz_offset = 0
        try:
            if tz_offset_raw is not None:
                tz_offset = int(tz_offset_raw)
        except ValueError:
            return HttpResponseBadRequest("tz_offset_minutes must be an integer")
        tzinfo = dt_timezone(timedelta(minutes=tz_offset))

        start = datetime.combine(target_date, datetime.min.time(), tzinfo=tzinfo)
        end = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=tzinfo)

        events = fetch_audit_events_between(agent, start=start, end=end)
        return JsonResponse({"count": len(events), "events": events}, safe=False)


class StaffAgentProcessEventsAPIView(SystemAdminAPIView):
    """Staff-only hook to enqueue a PROCESS_EVENTS run for an agent."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        try:
            process_agent_events_task.delay(str(agent.id))
            queued = True
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to queue process events for agent %s", agent.id)
            return JsonResponse({"error": "queue_failed", "detail": str(exc)}, status=500)

        processing_active = compute_processing_status(agent)
        return JsonResponse({"queued": queued, "processing_active": processing_active}, status=202)


class StaffAgentSystemMessageAPIView(SystemAdminAPIView):
    """Create a per-agent system directive for staff audit UI."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        body = (payload.get("body") or "").strip()
        if not body:
            return HttpResponseBadRequest("body is required")

        is_active = payload.get("is_active", True)
        message = PersistentAgentSystemMessage.objects.create(
            agent=agent,
            body=body,
            is_active=bool(is_active),
            created_by=request.user if request.user.is_authenticated else None,
        )

        return JsonResponse(serialize_system_message(message), status=201)


class StaffAgentSystemMessageDetailAPIView(SystemAdminAPIView):
    """Update an existing system directive from the staff audit UI."""

    http_method_names = ["patch"]

    def patch(self, request: HttpRequest, agent_id: str, message_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        message = get_object_or_404(PersistentAgentSystemMessage, pk=message_id, agent=agent)

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        updates: list[str] = []

        if "body" in payload:
            body = (payload.get("body") or "").strip()
            if not body:
                return HttpResponseBadRequest("body cannot be blank")
            if message.body != body:
                message.body = body
                updates.append("body")

        if "is_active" in payload:
            is_active = bool(payload.get("is_active"))
            if message.is_active != is_active:
                message.is_active = is_active
                updates.append("is_active")

        if updates:
            message.save(update_fields=updates)

        return JsonResponse(serialize_system_message(message))


class StaffPromptArchiveAPIView(SystemAdminAPIView):
    """Fetch and decompress a prompt archive payload for staff inspection."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, archive_id: str, *args: Any, **kwargs: Any):
        archive = get_object_or_404(PersistentAgentPromptArchive, pk=archive_id)
        if not default_storage.exists(archive.storage_key):
            return JsonResponse({"error": "missing"}, status=404)
        try:
            with default_storage.open(archive.storage_key, "rb") as stored:
                dctx = zstd.ZstdDecompressor()
                payload_bytes = dctx.decompress(stored.read())
        except Exception:
            logger.exception("Failed to read prompt archive %s", archive_id)
            return JsonResponse({"error": "read_failed"}, status=500)

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Failed to decode prompt archive payload for %s", archive_id, exc_info=True)
            payload = None

        return JsonResponse(
            {
                "id": str(archive.id),
                "agent_id": str(archive.agent_id),
                "rendered_at": archive.rendered_at.isoformat(),
                "tokens_before": archive.tokens_before,
                "tokens_after": archive.tokens_after,
                "tokens_saved": archive.tokens_saved,
                "payload": payload,
            }
        )


def _serialize_eval_task(task: EvalRunTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "sequence": task.sequence,
        "name": task.name,
        "status": task.status,
        "assertion_type": task.assertion_type,
        "expected_summary": task.expected_summary,
        "observed_summary": task.observed_summary,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "prompt_tokens": task.prompt_tokens,
        "completion_tokens": task.completion_tokens,
        "total_tokens": task.total_tokens,
        "cached_tokens": task.cached_tokens,
        "input_cost_total": float(task.input_cost_total),
        "input_cost_uncached": float(task.input_cost_uncached),
        "input_cost_cached": float(task.input_cost_cached),
        "output_cost": float(task.output_cost),
        "total_cost": float(task.total_cost),
        "credits_cost": float(task.credits_cost),
    }


def _task_counts(tasks: list[EvalRunTask]) -> dict[str, int | float | None]:
    totals: dict[str, int | float | None] = {
        "total": len(tasks),
        "completed": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": None,
    }
    for task in tasks:
        if task.status == EvalRunTask.Status.PASSED:
            totals["passed"] += 1
            totals["completed"] += 1
        elif task.status in (
            EvalRunTask.Status.FAILED,
            EvalRunTask.Status.ERRORED,
            EvalRunTask.Status.SKIPPED,
        ):
            totals["failed"] += 1
            totals["completed"] += 1
    if totals["completed"]:
        totals["pass_rate"] = totals["passed"] / totals["completed"]
    return totals


def _serialize_eval_run(run: EvalRun, *, include_tasks: bool = False) -> dict[str, Any]:
    tasks = list(run.tasks.all()) if include_tasks else []
    counts = _task_counts(tasks) if include_tasks else None

    payload: dict[str, Any] = {
        "id": str(run.id),
        "suite_run_id": str(run.suite_run_id) if run.suite_run_id else None,
        "scenario_slug": run.scenario_slug,
        "scenario_version": run.scenario_version,
        "scenario_fingerprint": run.scenario_fingerprint or None,
        "code_version": run.code_version or None,
        "code_branch": run.code_branch or None,
        "status": run.status,
        "run_type": run.run_type,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "agent_id": str(run.agent_id) if run.agent_id else None,
        "llm_routing_profile_name": run.llm_routing_profile_name or None,
        "primary_model": run.primary_model or None,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "cached_tokens": run.cached_tokens,
        "tokens_used": run.tokens_used,
        "input_cost_total": float(run.input_cost_total),
        "input_cost_uncached": float(run.input_cost_uncached),
        "input_cost_cached": float(run.input_cost_cached),
        "output_cost": float(run.output_cost),
        "total_cost": float(run.total_cost),
        "credits_cost": float(run.credits_cost),
        "completion_count": run.completion_count,
        "step_count": run.step_count,
    }

    if include_tasks:
        payload["tasks"] = [_serialize_eval_task(task) for task in tasks]
        payload["task_totals"] = counts

    return payload


def _serialize_suite_run(suite: EvalSuiteRun, *, include_runs: bool = False, include_tasks: bool = False) -> dict[str, Any]:
    runs = list(suite.runs.all()) if include_runs else []
    runs_payload = [_serialize_eval_run(run, include_tasks=include_tasks) for run in runs] if include_runs else []

    suite_task_totals = None
    if include_runs:
        all_tasks: list[EvalRunTask] = []
        for run in runs:
            all_tasks.extend(list(run.tasks.all()))
        suite_task_totals = _task_counts(all_tasks)

    aggregate_counts = {"total_runs": len(runs), "completed": 0, "errored": 0}
    for run in runs:
        if run.status == EvalRun.Status.COMPLETED:
            aggregate_counts["completed"] += 1
        elif run.status == EvalRun.Status.ERRORED:
            aggregate_counts["errored"] += 1

    cost_totals = None
    if include_runs:
        cost_totals = {
            "prompt_tokens": sum(r.prompt_tokens for r in runs),
            "completion_tokens": sum(r.completion_tokens for r in runs),
            "cached_tokens": sum(r.cached_tokens for r in runs),
            "tokens_used": sum(r.tokens_used for r in runs),
            "input_cost_total": float(sum(r.input_cost_total for r in runs)),
            "input_cost_uncached": float(sum(r.input_cost_uncached for r in runs)),
            "input_cost_cached": float(sum(r.input_cost_cached for r in runs)),
            "output_cost": float(sum(r.output_cost for r in runs)),
            "total_cost": float(sum(r.total_cost for r in runs)),
            "credits_cost": float(sum(r.credits_cost for r in runs)),
        }

    # Serialize the LLM routing profile if present
    llm_routing_profile = None
    if suite.llm_routing_profile_id:
        from console.llm_serializers import get_routing_profile_with_prefetch, serialize_routing_profile_detail
        try:
            profile = get_routing_profile_with_prefetch(str(suite.llm_routing_profile_id))
            llm_routing_profile = serialize_routing_profile_detail(profile)
        except Exception:
            # Fallback to basic info if prefetch fails
            llm_routing_profile = {
                "id": str(suite.llm_routing_profile_id),
                "name": suite.llm_routing_profile.name if suite.llm_routing_profile else None,
                "display_name": suite.llm_routing_profile.display_name if suite.llm_routing_profile else None,
            }

    return {
        "id": str(suite.id),
        "suite_slug": suite.suite_slug,
        "status": suite.status,
        "run_type": suite.run_type,
        "requested_runs": suite.requested_runs,
        "agent_strategy": suite.agent_strategy,
        "shared_agent_id": str(suite.shared_agent_id) if suite.shared_agent_id else None,
        "started_at": suite.started_at.isoformat() if suite.started_at else None,
        "finished_at": suite.finished_at.isoformat() if suite.finished_at else None,
        "runs": runs_payload if include_runs else None,
        "run_totals": aggregate_counts if include_runs else None,
        "task_totals": suite_task_totals if include_runs else None,
        "cost_totals": cost_totals if include_runs else None,
        "llm_routing_profile": llm_routing_profile,
    }


def _web_chat_properties(agent: PersistentAgent, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return analytics properties annotated with agent + organization context."""

    payload: dict[str, Any] = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
    }
    if extra:
        payload.update(extra)

    return Analytics.with_org_properties(payload, organization=getattr(agent, "organization", None))


@method_decorator(csrf_exempt, name="dispatch")
class AgentChatRosterAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def _resolve_override_for_agent(self, request: HttpRequest, agent_id: str) -> tuple[dict[str, str] | None, JsonResponse | None]:
        try:
            agent = (
                PersistentAgent.objects.non_eval()
                .select_related("organization")
                .get(pk=agent_id)
            )
        except PersistentAgent.DoesNotExist:
            return None, JsonResponse({"error": "Agent not found"}, status=404)

        if agent.organization_id:
            membership = (
                OrganizationMembership.objects.select_related("org")
                .filter(
                    user=request.user,
                    org_id=agent.organization_id,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                )
                .first()
            )
            if membership is None:
                if user_is_collaborator(request.user, agent):
                    return None, None
                return None, JsonResponse({"error": "Not permitted"}, status=403)
            return (
                {
                    "type": "organization",
                    "id": str(agent.organization_id),
                    "name": membership.org.name,
                },
                None,
            )

        if agent.user_id != request.user.id:
            if user_is_collaborator(request.user, agent):
                return None, None
            return None, JsonResponse({"error": "Not permitted"}, status=403)

        return (
            {
                "type": "personal",
                "id": str(agent.user_id),
                "name": request.user.get_full_name()
                or request.user.username
                or request.user.email
                or "Personal",
            },
            None,
        )

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        override = get_context_override(request)
        for_agent_id = request.GET.get("for_agent")
        if for_agent_id:
            override_for_agent, error_response = self._resolve_override_for_agent(request, for_agent_id)
            if error_response:
                return error_response
            override = override_for_agent

        context_info = resolve_console_context(
            request.user,
            request.session,
            override=override,
        )

        upgrade_url = None
        if settings.GOBII_PROPRIETARY_MODE:
            try:
                upgrade_url = reverse("proprietary:pricing")
            except NoReverseMatch:
                upgrade_url = None

        owner = request.user
        owner_type = "user"
        organization = None
        if context_info.current_context.type == "organization":
            organization = Organization.objects.filter(id=context_info.current_context.id).first()
            if organization:
                owner = organization
                owner_type = "organization"

        llm_intelligence = build_llm_intelligence_props(owner, owner_type, organization, upgrade_url)

        # Prefetch primary email and SMS endpoints for header display
        email_prefetch = models.Prefetch(
            "comms_endpoints",
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.EMAIL, is_primary=True),
            to_attr="primary_email_endpoints",
        )
        sms_prefetch = models.Prefetch(
            "comms_endpoints",
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.SMS),
            to_attr="primary_sms_endpoints",
        )
        agents_qs = (
            agent_queryset_for(request.user, context_info.current_context)
            .select_related("agent_color")
            .prefetch_related(email_prefetch, sms_prefetch)
            .order_by("name")
        )
        shared_qs = (
            shared_agent_queryset_for(request.user)
            .select_related("agent_color")
            .prefetch_related(email_prefetch, sms_prefetch)
        )
        agent_ids = list(agents_qs.values_list("id", flat=True))
        if agent_ids:
            shared_qs = shared_qs.exclude(id__in=agent_ids)
        agents = list(agents_qs)
        shared_agents = list(shared_qs.order_by("name"))
        collaborators_by_agent_id = {agent.id for agent in shared_agents}
        agents += shared_agents
        user = request.user
        org_memberships = OrganizationMembership.objects.filter(
            user=user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        org_ids = set(org_memberships.values_list("org_id", flat=True))
        admin_org_ids = set(
            org_memberships.filter(
                role__in=[
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                ]
            ).values_list("org_id", flat=True)
        )
        is_staff = bool(user.is_staff)
        def get_primary_email(agent: PersistentAgent) -> str | None:
            endpoints = getattr(agent, "primary_email_endpoints", None)
            if endpoints:
                return endpoints[0].address if endpoints else None
            return None

        def get_primary_sms(agent: PersistentAgent) -> str | None:
            endpoints = getattr(agent, "primary_sms_endpoints", None)
            if endpoints:
                return endpoints[0].address if endpoints else None
            return None

        payload = [
            {
                "id": str(agent.id),
                "name": agent.name or "",
                "avatar_url": agent.get_avatar_url(),
                "display_color_hex": agent.get_display_color(),
                "is_active": bool(agent.is_active),
                "short_description": agent.short_description or "",
                "is_org_owned": agent.organization_id is not None,
                "is_collaborator": agent.id in collaborators_by_agent_id,
                "can_manage_agent": (
                    is_staff
                    or agent.user_id == user.id
                    or (agent.organization_id and agent.organization_id in org_ids)
                ),
                "can_manage_collaborators": (
                    is_staff
                    or agent.user_id == user.id
                    or (agent.organization_id and agent.organization_id in admin_org_ids)
                ),
                "preferred_llm_tier": getattr(getattr(agent, "preferred_llm_tier", None), "key", None),
                "email": get_primary_email(agent),
                "sms": get_primary_sms(agent),
            }
            for agent in agents
        ]
        return JsonResponse(
            {
                "context": {
                    "type": context_info.current_context.type,
                    "id": context_info.current_context.id,
                    "name": context_info.current_context.name,
                },
                "agents": payload,
                "llmIntelligence": llm_intelligence,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentQuickCreateAPIView(LoginRequiredMixin, View):
    """API endpoint to create an agent from an initial message and return the agent ID."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        from console.agent_creation import create_persistent_agent_from_charter

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        initial_message = (body.get("message") or "").strip()
        if not initial_message:
            return JsonResponse({"error": "Message is required"}, status=400)
        preferred_llm_tier_key = (body.get("preferred_llm_tier") or "").strip() or None

        contact_email = (request.user.email or "").strip()
        if not contact_email:
            return JsonResponse({"error": "Please add an email address to continue"}, status=400)

        try:
            result = create_persistent_agent_from_charter(
                request,
                initial_message=initial_message,
                contact_email=contact_email,
                email_enabled=True,
                sms_enabled=False,
                preferred_contact_method="email",
                preferred_llm_tier_key=preferred_llm_tier_key,
            )
        except ValidationError as exc:
            error_messages = []
            if hasattr(exc, "message_dict"):
                for field_errors in exc.message_dict.values():
                    error_messages.extend(field_errors)
            error_messages.extend(getattr(exc, "messages", []))
            if not error_messages:
                error_messages.append("We couldn't create that agent. Please try again.")
            return JsonResponse({"error": error_messages[0]}, status=400)
        except IntegrityError:
            logger.exception("Error creating persistent agent via API")
            return JsonResponse({"error": "We ran into a problem creating your agent. Please try again."}, status=500)

        agent_email = None
        agent_email_endpoint = (
            result.agent.comms_endpoints.filter(channel=CommsChannel.EMAIL)
            .order_by("-is_primary")
            .first()
        )
        if agent_email_endpoint:
            agent_email = agent_email_endpoint.address

        return JsonResponse({
            "agent_id": str(result.agent.id),
            "agent_name": result.agent.name,
            "agent_email": agent_email,
        })


class AgentCollaboratorLeaveAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = PersistentAgent.objects.non_eval().filter(pk=agent_id).first()
        if not agent:
            return JsonResponse({"error": "Agent not found"}, status=404)

        if not user_is_collaborator(request.user, agent):
            return JsonResponse({"error": "Not a collaborator"}, status=403)

        collaborator = (
            AgentCollaborator.objects
            .filter(agent=agent, user=request.user)
            .select_related("user")
            .first()
        )
        if collaborator:
            collaborator_props = Analytics.with_org_properties(
                {
                    "agent_id": str(agent.id),
                    "agent_name": agent.name,
                    "collaborator_id": str(collaborator.id),
                    "collaborator_user_id": str(request.user.id),
                    "collaborator_email": request.user.email or "",
                    "invited_by_id": str(collaborator.invited_by_id) if collaborator.invited_by_id else "",
                    "actor_id": str(request.user.id),
                },
                organization=getattr(agent, "organization", None),
            )
            collaborator.delete()
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.AGENT_COLLABORATOR_LEFT,
                source=AnalyticsSource.WEB,
                properties=collaborator_props.copy(),
            ))
        return JsonResponse({"success": True})


def _extract_phone_form_error(form: PhoneAddForm) -> str:
    error_msg = "Enter a valid phone number."
    try:
        data = form.errors.as_data()
        for errors in data.values():
            for err in errors:
                if getattr(err, "code", None) == "unsupported_region":
                    return "Phone numbers from this country are not yet supported."
    except Exception:
        pass
    return error_msg


class UserPhoneAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post", "delete"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        phone = get_primary_phone(request.user)
        return JsonResponse({"phone": serialize_phone(phone)})

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if get_primary_phone(request.user):
            return JsonResponse(
                {"error": "Delete your existing phone number before adding a new one."},
                status=400,
            )

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        raw_number = (body.get("phone_number") or body.get("phoneNumber") or body.get("phone") or "").strip()
        if not raw_number:
            return JsonResponse({"error": "Phone number is required."}, status=400)

        form = PhoneAddForm(
            {
                "phone_number": raw_number,
                "phone_number_hidden": raw_number,
            },
            user=request.user,
        )

        if not form.is_valid():
            return JsonResponse({"error": _extract_phone_form_error(form)}, status=400)

        try:
            phone = form.save()
        except IntegrityError:
            return JsonResponse({"error": "This phone number is already in use."}, status=400)
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to add phone number."
            return JsonResponse({"error": message_text}, status=400)

        return JsonResponse({"phone": serialize_phone(phone)})

    def delete(self, request: HttpRequest, *args: Any, **kwargs: Any):
        phone = get_primary_phone(request.user)
        if phone:
            phone.delete()
        return JsonResponse({"phone": None})


class UserPhoneVerifyAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        phone = get_primary_phone(request.user)
        if not phone:
            return JsonResponse({"error": "Add a phone number first."}, status=400)

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        code = (body.get("verification_code") or body.get("code") or "").strip()
        if not code:
            return JsonResponse({"error": "Verification code is required."}, status=400)

        form = PhoneVerifyForm(
            {
                "phone_number": phone.phone_number,
                "verification_code": code,
            },
            user=request.user,
        )

        if not form.is_valid():
            error_msg = next(iter(form.errors.values()))[0] if form.errors else "Invalid verification code."
            return JsonResponse({"error": error_msg}, status=400)

        try:
            verified_phone = form.save()
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to verify code."
            return JsonResponse({"error": message_text}, status=400)

        return JsonResponse({"phone": serialize_phone(verified_phone)})


class UserPhoneResendAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        phone = get_primary_phone(request.user)
        if not phone:
            return JsonResponse({"error": "Add a phone number first."}, status=400)
        if phone.is_verified:
            return JsonResponse({"phone": serialize_phone(phone)})

        remaining = get_phone_cooldown_remaining(phone)
        if remaining == 0:
            try:
                sid = sms.start_verification(phone_number=phone.phone_number)
            except Exception as exc:
                return JsonResponse({"error": f"Failed to resend verification: {exc}"}, status=400)
            phone.last_verification_attempt = timezone.now()
            phone.verification_sid = sid
            phone.save(update_fields=["last_verification_attempt", "verification_sid", "updated_at"])

        return JsonResponse({"phone": serialize_phone(phone)})


class UserEmailResendVerificationAPIView(ApiLoginRequiredMixin, View):
    """Resend email verification for the current user's primary email."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        from allauth.account.models import EmailAddress
        from allauth.account.utils import send_email_confirmation

        email_address = EmailAddress.objects.filter(user=request.user, primary=True).first()
        if not email_address:
            return JsonResponse({"error": "No email address found."}, status=400)

        if email_address.verified:
            return JsonResponse({"verified": True, "message": "Email already verified."})

        try:
            send_email_confirmation(request, request.user, email=email_address.email)
        except Exception as exc:
            logger.exception("Failed to send email verification for user %s", request.user.id)
            return JsonResponse({"error": f"Failed to send verification email: {exc}"}, status=500)

        return JsonResponse({"verified": False, "message": "Verification email sent."})


class AgentSmsEnableAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        phone = get_primary_phone(request.user)
        if not phone or not phone.is_verified:
            return JsonResponse({"error": "Please verify a phone number before enabling SMS."}, status=400)

        try:
            agent_sms_endpoint, _ = enable_agent_sms_contact(agent, phone)
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to enable SMS."
            return JsonResponse({"error": message_text}, status=400)

        return JsonResponse({
            "agentSms": {"number": agent_sms_endpoint.address},
            "userPhone": serialize_phone(phone),
            "preferredContactMethod": "sms",
        })


class AgentReassignAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        target_org_id = (body.get("target_org_id") or body.get("targetOrgId") or "").strip() or None

        try:
            result = reassign_agent_organization(request, agent, target_org_id)
        except PermissionDenied as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=403)
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to reassign agent."
            return JsonResponse({"success": False, "error": message_text}, status=400)
        except Exception:
            logger.exception("Failed to reassign agent %s", agent.id)
            return JsonResponse({"success": False, "error": "An unexpected error occurred."}, status=500)

        return JsonResponse({
            "success": True,
            **result,
        })


class AgentTemplateCloneAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        handle = (body.get("handle") or "").strip() or None

        try:
            result = TemplateCloneService.clone_agent_to_template(
                agent=agent,
                user=request.user,
                requested_handle=handle,
            )
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Invalid handle."
            return JsonResponse({"error": message_text}, status=400)
        except TemplateCloneError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception:
            logger.exception("Failed to clone template for agent %s", agent.id)
            return JsonResponse({"error": "An unexpected error occurred."}, status=500)

        template = result.template
        if not template.slug or not result.public_profile.handle:
            return JsonResponse({"error": "Template URL could not be generated."}, status=500)

        template_url = request.build_absolute_uri(f"/{result.public_profile.handle}/{template.slug}/")
        return JsonResponse({
            "created": result.created,
            "templateUrl": template_url,
            "templateSlug": template.slug,
            "publicProfileHandle": result.public_profile.handle,
            "displayName": template.display_name,
        })


@method_decorator(csrf_exempt, name="dispatch")
class AgentTimelineAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        direction_raw = (request.GET.get("direction") or "initial").lower()
        direction: TimelineDirection
        if direction_raw not in {"initial", "older", "newer"}:
            return HttpResponseBadRequest("Invalid direction parameter")
        direction = direction_raw  # type: ignore[assignment]
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)

        cursor = request.GET.get("cursor") or None
        try:
            limit = int(request.GET.get("limit", DEFAULT_PAGE_SIZE))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")

        window = fetch_timeline_window(
            agent,
            cursor=cursor,
            direction=direction,
            limit=limit,
        )
        payload = {
            "events": window.events,
            "oldest_cursor": window.oldest_cursor,
            "newest_cursor": window.newest_cursor,
            "has_more_older": window.has_more_older,
            "has_more_newer": window.has_more_newer,
            "processing_active": window.processing_active,
            "processing_snapshot": serialize_processing_snapshot(window.processing_snapshot),
            "agent_color_hex": agent.get_display_color(),
            "agent_name": agent.name,
            "agent_avatar_url": agent.get_avatar_url(),
        }
        return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class AgentMessageCreateAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        attachments: list[Any] = []
        message_text = ""
        if request.content_type and request.content_type.startswith("multipart/form-data"):
            message_text = (request.POST.get("body") or "").strip()
            attachments = list(request.FILES.getlist("attachments") or request.FILES.values())
        else:
            try:
                body = json.loads(request.body or "{}")
            except json.JSONDecodeError:
                return HttpResponseBadRequest("Invalid JSON body")
            message_text = (body.get("body") or "").strip()

        if not message_text and not attachments:
            return HttpResponseBadRequest("Message body or attachment is required")

        sender_address, recipient_address = _ensure_console_endpoints(agent, request.user)

        # Keep the web session alive whenever the user sends a message from the console UI.
        session_result = touch_web_session(
            agent,
            request.user,
            source="message",
            create=True,
            ttl_seconds=WEB_SESSION_TTL_SECONDS,
        )

        if not agent.is_sender_whitelisted(CommsChannel.WEB, sender_address):
            return HttpResponseForbidden("You are not allowed to message this agent.")

        parsed = ParsedMessage(
            sender=sender_address,
            recipient=recipient_address,
            subject=None,
            body=message_text,
            attachments=attachments,
            raw_payload={"source": "console", "user_id": request.user.id},
            msg_channel=CommsChannel.WEB,
        )
        info = ingest_inbound_message(CommsChannel.WEB, parsed, filespace_import_mode="sync")
        event = serialize_message_event(info.message)

        props = {
            "message_id": str(info.message.id),
            "message_length": len(message_text),
            "attachments_count": len(attachments),
        }
        if session_result:
            props["session_key"] = str(session_result.session.session_key)
            props["session_ttl_seconds"] = session_result.ttl_seconds

        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.WEB_CHAT_MESSAGE_SENT,
            source=AnalyticsSource.WEB,
            properties=_web_chat_properties(agent, props),
        )

        return JsonResponse({"event": event}, status=201)


def _build_filespace_download_response(node: AgentFsNode) -> FileResponse:
    file_field = node.content
    if not file_field or not getattr(file_field, "name", None):
        raise Http404("File not found.")

    storage = file_field.storage
    name = file_field.name
    if hasattr(storage, "exists") and not storage.exists(name):
        raise Http404("File not found.")
    try:
        file_handle = storage.open(name, "rb")
    except (FileNotFoundError, OSError):
        raise Http404("File not found.")

    content_type = node.mime_type or mimetypes.guess_type(node.name or "")[0] or "application/octet-stream"
    # Images render inline (for markdown/HTML embedding), other files download
    is_image = content_type.startswith("image/")
    response = FileResponse(
        file_handle,
        as_attachment=not is_image,
        filename=node.name or "download",
        content_type=content_type,
    )
    response["Cache-Control"] = "private, max-age=300"
    return response


class AgentFsNodeDownloadAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def _has_access(self, user, agent: PersistentAgent) -> bool:
        if user_can_manage_agent(user, agent):
            return True
        return user_is_collaborator(user, agent)

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent.objects.select_related("organization"), pk=agent_id)
        if not self._has_access(request.user, agent):
            return HttpResponseForbidden("Not authorized to access this file.")

        node_id = (request.GET.get("node_id") or "").strip()
        path = (request.GET.get("path") or "").strip()
        if not node_id and not path:
            return HttpResponseBadRequest("node_id or path is required")

        filespace_ids = AgentFileSpaceAccess.objects.filter(agent=agent).values_list("filespace_id", flat=True)
        try:
            if node_id:
                node = (
                    AgentFsNode.objects
                    .filter(
                        id=node_id,
                        filespace_id__in=filespace_ids,
                        node_type=AgentFsNode.NodeType.FILE,
                        is_deleted=False,
                    )
                    .first()
                )
            else:
                matches = AgentFsNode.objects.filter(
                    filespace_id__in=filespace_ids,
                    path=path,
                    node_type=AgentFsNode.NodeType.FILE,
                    is_deleted=False,
                )
                if matches.count() > 1:
                    return HttpResponseBadRequest("Multiple files match path; use node_id instead.")
                node = matches.first()
        except (ValueError, ValidationError):
            return HttpResponseBadRequest("Invalid node_id")
        if not node:
            raise Http404("File not found.")

        try:
            parent_path, _ = _path_meta(node.path)
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FILE_DOWNLOADED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": str(node.filespace_id),
                        "node_id": str(node.id),
                        "parent_path": parent_path,
                        "path": node.path,
                        "mime_type": node.mime_type or None,
                        "size_bytes": node.size_bytes,
                        "download_type": "direct",
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logger.debug("Failed to emit download analytics for agent %s node %s", agent.id, getattr(node, "id", None), exc_info=True)
        return _build_filespace_download_response(node)


class SignedAgentFsNodeDownloadAPIView(View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, token: str, *args: Any, **kwargs: Any):
        payload = load_signed_filespace_download_payload(token)
        if not payload:
            raise Http404("File not found.")

        agent_id = payload.get("agent_id")
        node_id = payload.get("node_id")
        try:
            agent_uuid = uuid.UUID(str(agent_id))
            node_uuid = uuid.UUID(str(node_id))
        except (TypeError, ValueError):
            raise Http404("File not found.")

        filespace_ids = AgentFileSpaceAccess.objects.filter(
            agent_id=agent_uuid
        ).values_list("filespace_id", flat=True)
        node = (
            AgentFsNode.objects
            .filter(
                id=node_uuid,
                filespace_id__in=filespace_ids,
                node_type=AgentFsNode.NodeType.FILE,
                is_deleted=False,
            )
            .first()
        )
        if not node:
            raise Http404("File not found.")

        try:
            parent_path, _ = _path_meta(node.path)
            owner_user_id = getattr(getattr(node.filespace, "owner_user", None), "id", None)
            Analytics.track_event(
                user_id=str(owner_user_id or payload.get("agent_id") or ""),
                event=AnalyticsEvent.AGENT_FILE_DOWNLOADED,
                source=AnalyticsSource.WEB,
                properties={
                    "agent_id": str(agent_uuid),
                    "filespace_id": str(node.filespace_id),
                    "node_id": str(node.id),
                    "parent_path": parent_path,
                    "path": node.path,
                    "mime_type": node.mime_type or None,
                    "size_bytes": node.size_bytes,
                    "download_type": "signed",
                },
            )
        except Exception:
            logger.debug("Failed to emit signed download analytics for node %s", getattr(node, "id", None), exc_info=True)
        return _build_filespace_download_response(node)


def _serialize_agent_fs_node(node: AgentFsNode) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "parentId": str(node.parent_id) if node.parent_id else None,
        "name": node.name,
        "path": node.path,
        "nodeType": node.node_type,
        "sizeBytes": node.size_bytes,
        "mimeType": node.mime_type or None,
        "createdAt": node.created_at.isoformat() if node.created_at else None,
        "updatedAt": node.updated_at.isoformat() if node.updated_at else None,
    }


class AgentFsNodeListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        filespace = get_or_create_default_filespace(agent)
        nodes = (
            AgentFsNode.objects
            .filter(filespace=filespace, is_deleted=False)
            .only(
                "id",
                "parent_id",
                "name",
                "path",
                "node_type",
                "size_bytes",
                "mime_type",
                "created_at",
                "updated_at",
            )
            .order_by("parent_id", "node_type", "name")
        )

        try:
            node_count = nodes.count()
            file_count = nodes.filter(node_type=AgentFsNode.NodeType.FILE).count()
            dir_count = node_count - file_count
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FILES_VIEWED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": str(filespace.id),
                        "node_count": node_count,
                        "file_count": file_count,
                        "dir_count": dir_count,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logger.debug("Failed to emit agent files viewed analytics for agent %s", agent.id, exc_info=True)

        payload = {
            "filespace": {"id": str(filespace.id), "name": filespace.name},
            "nodes": [_serialize_agent_fs_node(node) for node in nodes],
        }
        return JsonResponse(payload)


class AgentFsNodeUploadAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        files = list(request.FILES.getlist("files")) or list(request.FILES.getlist("file"))
        if not files:
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FILES_UPLOAD_FAILED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": None,
                        "reason_code": "no_files",
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            return HttpResponseBadRequest("files are required")

        filespace = get_or_create_default_filespace(agent)
        parent = None
        parent_id = (request.POST.get("parent_id") or "").strip()
        parent_path = (request.POST.get("parent_path") or "").strip()

        if parent_id:
            parent = (
                AgentFsNode.objects
                .filter(
                    filespace=filespace,
                    id=parent_id,
                    node_type=AgentFsNode.NodeType.DIR,
                    is_deleted=False,
                )
                .first()
            )
            if not parent:
                Analytics.track_event(
                    user_id=str(request.user.id),
                    event=AnalyticsEvent.AGENT_FILES_UPLOAD_FAILED,
                    source=AnalyticsSource.WEB,
                    properties=Analytics.with_org_properties(
                        {
                            "agent_id": str(agent.id),
                            "filespace_id": str(filespace.id),
                            "reason_code": "invalid_parent",
                        },
                        organization=getattr(agent, "organization", None),
                    ),
                )
                return HttpResponseBadRequest("parent_id is invalid")
        elif parent_path:
            parent = (
                AgentFsNode.objects
                .filter(
                    filespace=filespace,
                    path=parent_path,
                    node_type=AgentFsNode.NodeType.DIR,
                    is_deleted=False,
                )
                .first()
            )
            if not parent:
                Analytics.track_event(
                    user_id=str(request.user.id),
                    event=AnalyticsEvent.AGENT_FILES_UPLOAD_FAILED,
                    source=AnalyticsSource.WEB,
                    properties=Analytics.with_org_properties(
                        {
                            "agent_id": str(agent.id),
                            "filespace_id": str(filespace.id),
                            "reason_code": "invalid_parent",
                        },
                        organization=getattr(agent, "organization", None),
                    ),
                )
                return HttpResponseBadRequest("parent_path is invalid")

        created = []
        total_bytes = 0
        for upload in files:
            base_name = get_valid_filename(os.path.basename(upload.name or "")) or "file"
            name = dedupe_name(filespace, parent, base_name)
            node = AgentFsNode(
                filespace=filespace,
                parent=parent,
                node_type=AgentFsNode.NodeType.FILE,
                name=name,
                created_by_agent=agent,
                mime_type=getattr(upload, "content_type", "") or "",
            )
            node.save()
            node.content.save(name, upload, save=True)
            node.refresh_from_db()
            created.append(_serialize_agent_fs_node(node))
            try:
                total_bytes += int(getattr(upload, "size", 0) or 0)
            except Exception:
                pass

        try:
            parent_path_val = parent.path if parent else "/"
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FILES_UPLOADED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": str(filespace.id),
                        "parent_path": parent_path_val,
                        "file_count": len(created),
                        "total_bytes": total_bytes,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logger.debug("Failed to emit upload analytics for agent %s", agent.id, exc_info=True)
        return JsonResponse({"created": created}, status=201)


class AgentFsNodeBulkDeleteAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        if not user_can_manage_agent(request.user, agent):
            return HttpResponseForbidden("Not authorized to delete files.")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        node_ids = payload.get("node_ids") or payload.get("nodeIds") or []
        if not isinstance(node_ids, list) or not node_ids:
            return HttpResponseBadRequest("node_ids must be a non-empty list")

        filespace = get_or_create_default_filespace(agent)
        nodes = (
            AgentFsNode.objects
            .filter(
                filespace=filespace,
                id__in=node_ids,
                node_type=AgentFsNode.NodeType.FILE,
                is_deleted=False,
            )
        )

        deleted = 0
        for node in nodes:
            deleted += node.trash_subtree()

        try:
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FILES_DELETED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": str(filespace.id),
                        "deleted_count": deleted,
                        "requested_count": len(node_ids),
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logger.debug("Failed to emit delete analytics for agent %s", agent.id, exc_info=True)
        return JsonResponse({"deleted": deleted})


class AgentFsNodeCreateDirAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        if not user_can_manage_agent(request.user, agent):
            return HttpResponseForbidden("Not authorized to create folders.")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        name = str(payload.get("name") or "").strip()
        if not name:
            return HttpResponseBadRequest("name is required")

        parent_id = (payload.get("parent_id") or payload.get("parentId") or "").strip()
        filespace = get_or_create_default_filespace(agent)
        parent = None
        if parent_id:
            parent = (
                AgentFsNode.objects
                .filter(
                    filespace=filespace,
                    id=parent_id,
                    node_type=AgentFsNode.NodeType.DIR,
                    is_deleted=False,
                )
                .first()
            )
            if not parent:
                return HttpResponseBadRequest("parent_id is invalid")

        if AgentFsNode.objects.filter(filespace=filespace, parent=parent, name=name, is_deleted=False).exists():
            return HttpResponseBadRequest("folder already exists")

        node = AgentFsNode(
            filespace=filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.DIR,
            name=name,
            created_by_agent=agent,
        )
        try:
            node.save()
        except ValidationError as exc:
            return HttpResponseBadRequest(str(exc))
        except IntegrityError:
            return HttpResponseBadRequest("Unable to create folder due to a name conflict")

        try:
            parent_path, _ = _path_meta(node.path)
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FOLDER_CREATED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": str(filespace.id),
                        "node_id": str(node.id),
                        "parent_path": parent_path,
                        "path": node.path,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logger.debug("Failed to emit folder create analytics for agent %s", agent.id, exc_info=True)
        return JsonResponse({"node": _serialize_agent_fs_node(node)}, status=201)


class AgentFsNodeMoveAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        if not user_can_manage_agent(request.user, agent):
            return HttpResponseForbidden("Not authorized to move files.")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        node_id = str(payload.get("node_id") or payload.get("nodeId") or "").strip()
        if not node_id:
            return HttpResponseBadRequest("node_id is required")

        parent_id = payload.get("parent_id") or payload.get("parentId")
        if isinstance(parent_id, str):
            parent_id = parent_id.strip()
        if not parent_id:
            parent_id = None

        filespace = get_or_create_default_filespace(agent)
        node = (
            AgentFsNode.objects
            .filter(filespace=filespace, id=node_id, is_deleted=False)
            .first()
        )
        if not node:
            return HttpResponseBadRequest("node_id is invalid")

        old_parent = node.parent
        old_parent_path = old_parent.path if old_parent else "/"
        parent = None
        if parent_id:
            parent = (
                AgentFsNode.objects
                .filter(
                    filespace=filespace,
                    id=parent_id,
                    node_type=AgentFsNode.NodeType.DIR,
                    is_deleted=False,
                )
                .first()
            )
            if not parent:
                return HttpResponseBadRequest("parent_id is invalid")

        if node.parent_id == (parent.id if parent else None):
            return JsonResponse({"node": _serialize_agent_fs_node(node)})

        name_conflict = (
            AgentFsNode.objects
            .filter(filespace=filespace, parent=parent, name=node.name, is_deleted=False)
            .exclude(id=node.id)
            .exists()
        )
        if name_conflict:
            return HttpResponseBadRequest("A node with that name already exists in the destination folder.")

        node.parent = parent
        try:
            node.save()
        except ValidationError as exc:
            return HttpResponseBadRequest(str(exc))
        except IntegrityError:
            return HttpResponseBadRequest("Unable to move node due to a name conflict")

        try:
            new_parent_path = parent.path if parent else "/"
            parent_path, _ = _path_meta(node.path)
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FILE_MOVED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": str(filespace.id),
                        "node_id": str(node.id),
                        "from_parent_path": old_parent_path,
                        "to_parent_path": new_parent_path,
                        "path": node.path,
                        "parent_path": parent_path,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logger.debug("Failed to emit move analytics for agent %s", agent.id, exc_info=True)
        return JsonResponse({"node": _serialize_agent_fs_node(node)})


class ConsoleLLMOverviewAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        payload = build_llm_overview()
        return JsonResponse(payload)


class LLMProviderListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        display_name = (payload.get("display_name") or "").strip()
        key = (payload.get("key") or "").strip()
        if not display_name or not key:
            return HttpResponseBadRequest("display_name and key are required")

        if LLMProvider.objects.filter(key=key).exists():
            return HttpResponseBadRequest("Provider key already exists")

        provider = LLMProvider(
            display_name=display_name,
            key=key,
            enabled=_coerce_bool(payload.get("enabled", True)),
            env_var_name=(payload.get("env_var_name") or "").strip(),
            model_prefix=(payload.get("model_prefix") or "").strip(),
            browser_backend=payload.get("browser_backend") or LLMProvider.BrowserBackend.OPENAI,
            supports_safety_identifier=_coerce_bool(payload.get("supports_safety_identifier", False)),
            vertex_project=(payload.get("vertex_project") or "").strip(),
            vertex_location=(payload.get("vertex_location") or "").strip(),
        )
        api_key_value = payload.get("api_key")
        if api_key_value:
            provider.api_key_encrypted = SecretsEncryption.encrypt_value(api_key_value)
        provider.save()
        return _json_ok(provider_id=str(provider.id))


class LLMProviderDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, provider_id: str, *args: Any, **kwargs: Any):
        provider = get_object_or_404(LLMProvider, pk=provider_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        updatable_fields = {
            "display_name": "display_name",
            "env_var_name": "env_var_name",
            "model_prefix": "model_prefix",
            "browser_backend": "browser_backend",
            "supports_safety_identifier": "supports_safety_identifier",
            "vertex_project": "vertex_project",
            "vertex_location": "vertex_location",
        }
        for field, model_field in updatable_fields.items():
            if field in payload:
                value = payload.get(field)
                if isinstance(value, str):
                    value = value.strip()
                if model_field == "supports_safety_identifier":
                    value = _coerce_bool(value)
                setattr(provider, model_field, value)

        if "enabled" in payload:
            provider.enabled = _coerce_bool(payload.get("enabled"))

        api_key_value = payload.get("api_key")
        if api_key_value:
            provider.api_key_encrypted = SecretsEncryption.encrypt_value(api_key_value)
        if payload.get("clear_api_key"):
            provider.api_key_encrypted = None

        provider.save()
        return _json_ok(provider_id=str(provider.id))

    def delete(self, request: HttpRequest, provider_id: str, *args: Any, **kwargs: Any):
        provider = get_object_or_404(LLMProvider, pk=provider_id)
        has_dependents = (
            provider.persistent_endpoints.exists()
            or provider.browser_endpoints.exists()
            or provider.embedding_endpoints.exists()
            or provider.file_handler_endpoints.exists()
        )
        if has_dependents:
            return HttpResponseBadRequest("Provider cannot be deleted while endpoints exist")
        provider.delete()
        return _json_ok()


class LLMEndpointTestAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_id = payload.get("endpoint_id")
        kind = (payload.get("kind") or "persistent").strip().lower()
        if not endpoint_id:
            return HttpResponseBadRequest("endpoint_id is required")

        try:
            if kind == "persistent":
                endpoint = get_object_or_404(PersistentModelEndpoint, pk=endpoint_id)
                result = _run_completion_test(
                    endpoint,
                    endpoint.provider,
                    model_attr="litellm_model",
                    base_attr="api_base",
                    default_max_tokens=128,
                )
            elif kind == "browser":
                endpoint = get_object_or_404(BrowserModelEndpoint, pk=endpoint_id)
                result = _run_completion_test(
                    endpoint,
                    endpoint.provider,
                    model_attr="browser_model",
                    base_attr="browser_base_url",
                    default_max_tokens=endpoint.max_output_tokens or 128,
                )
            elif kind == "embedding":
                endpoint = get_object_or_404(EmbeddingsModelEndpoint, pk=endpoint_id)
                result = _run_embedding_test(endpoint)
            elif kind == "file_handler":
                endpoint = get_object_or_404(FileHandlerModelEndpoint, pk=endpoint_id)
                result = _run_completion_test(
                    endpoint,
                    endpoint.provider,
                    model_attr="litellm_model",
                    base_attr="api_base",
                    default_max_tokens=128,
                )
            else:
                return HttpResponseBadRequest("Invalid endpoint kind")
        except ValueError as exc:
            return JsonResponse({"ok": False, "message": str(exc)}, status=400)
        except Exception as exc:
            logger.warning(
                "LLM endpoint test failed",
                exc_info=True,
            )
            return JsonResponse({"ok": False, "message": f"{type(exc).__name__}: {exc}"}, status=400)

        return JsonResponse({"ok": True, **result})


class PersistentEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        provider_id = payload.get("provider_id")
        provider = get_object_or_404(LLMProvider, pk=provider_id)
        key = (payload.get("key") or "").strip()
        model = (payload.get("model") or payload.get("litellm_model") or "").strip()
        if not key or not model:
            return HttpResponseBadRequest("key and model are required")
        if PersistentModelEndpoint.objects.filter(key=key).exists():
            return HttpResponseBadRequest("Endpoint key already exists")
        if provider.model_prefix and model.startswith(provider.model_prefix):
            return HttpResponseBadRequest("Store persistent models without the provider prefix; it is applied at runtime.")

        temp_value = payload.get("temperature_override")
        temperature_override = None
        if temp_value not in (None, ""):
            temperature_override = float(temp_value)
        try:
            reasoning_effort = _coerce_reasoning_effort(payload.get("reasoning_effort"))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint = PersistentModelEndpoint.objects.create(
            key=key,
            provider=provider,
            litellm_model=model,
            temperature_override=temperature_override,
            supports_temperature=_coerce_bool(payload.get("supports_temperature", True)),
            supports_tool_choice=_coerce_bool(payload.get("supports_tool_choice", True)),
            use_parallel_tool_calls=_coerce_bool(payload.get("use_parallel_tool_calls", True)),
            supports_vision=_coerce_bool(payload.get("supports_vision", False)),
            supports_reasoning=_coerce_bool(payload.get("supports_reasoning", False)),
            reasoning_effort=reasoning_effort,
            api_base=(payload.get("api_base") or "").strip(),
            openrouter_preset=(payload.get("openrouter_preset") or "").strip(),
            low_latency=_coerce_bool(payload.get("low_latency", False)),
            enabled=_coerce_bool(payload.get("enabled", True)),
        )
        invalidate_llm_bootstrap_cache()
        return _json_ok(endpoint_id=str(endpoint.id))


class PersistentEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(PersistentModelEndpoint, pk=endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "model" in payload or "litellm_model" in payload:
            model = (payload.get("model") or payload.get("litellm_model") or "").strip()
            if model:
                if endpoint.provider and endpoint.provider.model_prefix and model.startswith(endpoint.provider.model_prefix):
                    return HttpResponseBadRequest("Store persistent models without the provider prefix; it is applied at runtime.")
                endpoint.litellm_model = model
        if "temperature_override" in payload:
            temp = payload.get("temperature_override")
            if temp in (None, ""):
                endpoint.temperature_override = None
            else:
                endpoint.temperature_override = float(temp)
        if "supports_temperature" in payload:
            endpoint.supports_temperature = _coerce_bool(payload.get("supports_temperature"))
        if "supports_tool_choice" in payload:
            endpoint.supports_tool_choice = _coerce_bool(payload.get("supports_tool_choice"))
        if "use_parallel_tool_calls" in payload:
            endpoint.use_parallel_tool_calls = _coerce_bool(payload.get("use_parallel_tool_calls"))
        if "supports_vision" in payload:
            endpoint.supports_vision = _coerce_bool(payload.get("supports_vision"))
        if "low_latency" in payload:
            endpoint.low_latency = _coerce_bool(payload.get("low_latency"))
        if "supports_reasoning" in payload:
            endpoint.supports_reasoning = _coerce_bool(payload.get("supports_reasoning"))
        if "reasoning_effort" in payload:
            try:
                reasoning_effort = _coerce_reasoning_effort(payload.get("reasoning_effort"))
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
            endpoint.reasoning_effort = reasoning_effort
        if "api_base" in payload:
            endpoint.api_base = (payload.get("api_base") or "").strip()
        if "openrouter_preset" in payload:
            endpoint.openrouter_preset = (payload.get("openrouter_preset") or "").strip()
        if "max_input_tokens" in payload:
            val = payload.get("max_input_tokens")
            if val in (None, "", "auto", "automatic"):
                endpoint.max_input_tokens = None
            else:
                try:
                    endpoint.max_input_tokens = int(val)
                except (TypeError, ValueError):
                    return HttpResponseBadRequest("max_input_tokens must be an integer or 'automatic'")
        if "enabled" in payload:
            endpoint.enabled = _coerce_bool(payload.get("enabled"))
        endpoint.save()
        invalidate_llm_bootstrap_cache()
        # Invalidate the min endpoint input tokens cache when max_input_tokens changes
        from api.agent.core.llm_config import invalidate_min_endpoint_input_tokens_cache
        invalidate_min_endpoint_input_tokens_cache()
        return _json_ok(endpoint_id=str(endpoint.id))

    def delete(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(PersistentModelEndpoint, pk=endpoint_id)
        if endpoint.in_tiers.exists():
            return HttpResponseBadRequest("Remove endpoint from tiers before deleting")
        endpoint.delete()
        invalidate_llm_bootstrap_cache()
        from api.agent.core.llm_config import invalidate_min_endpoint_input_tokens_cache
        invalidate_min_endpoint_input_tokens_cache()
        return _json_ok()


class PersistentTokenRangeListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        name = (payload.get("name") or "").strip()
        if not name:
            return HttpResponseBadRequest("name is required")
        min_tokens = payload.get("min_tokens")
        max_tokens = payload.get("max_tokens")
        try:
            min_tokens_int = int(min_tokens)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("min_tokens must be an integer")
        max_tokens_int = None
        if max_tokens not in (None, ""):
            try:
                max_tokens_int = int(max_tokens)
            except (TypeError, ValueError):
                return HttpResponseBadRequest("max_tokens must be an integer or null")
            if max_tokens_int <= min_tokens_int:
                return HttpResponseBadRequest("max_tokens must be greater than min_tokens")

        token_range = PersistentTokenRange.objects.create(
            name=name,
            min_tokens=min_tokens_int,
            max_tokens=max_tokens_int,
        )
        invalidate_llm_bootstrap_cache()
        return _json_ok(token_range_id=str(token_range.id))


class PersistentTokenRangeDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        token_range = get_object_or_404(PersistentTokenRange, pk=range_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "name" in payload:
            name = (payload.get("name") or "").strip()
            if name:
                token_range.name = name
        if "min_tokens" in payload:
            try:
                token_range.min_tokens = int(payload.get("min_tokens"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("min_tokens must be an integer")
        if "max_tokens" in payload:
            max_tokens = payload.get("max_tokens")
            if max_tokens in (None, ""):
                token_range.max_tokens = None
            else:
                try:
                    token_range.max_tokens = int(max_tokens)
                except (TypeError, ValueError):
                    return HttpResponseBadRequest("max_tokens must be an integer")
        if token_range.max_tokens is not None and token_range.max_tokens <= token_range.min_tokens:
            return HttpResponseBadRequest("max_tokens must be greater than min_tokens")

        token_range.save()
        invalidate_llm_bootstrap_cache()
        return _json_ok(token_range_id=str(token_range.id))

    def delete(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        token_range = get_object_or_404(PersistentTokenRange, pk=range_id)
        token_range.delete()
        invalidate_llm_bootstrap_cache()
        return _json_ok()


class PersistentTierListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        token_range = get_object_or_404(PersistentTokenRange, pk=range_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        try:
            intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        description = (payload.get("description") or "").strip()
        order = _next_order_for_range(token_range, intelligence_tier)

        tier = PersistentLLMTier.objects.create(
            token_range=token_range,
            order=order,
            description=description,
            intelligence_tier=intelligence_tier,
        )
        invalidate_llm_bootstrap_cache()
        return _json_ok(tier_id=str(tier.id))


class PersistentTierDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(PersistentLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        if "move" in payload:
            direction = (payload.get("move") or "").lower()
            if direction not in {"up", "down"}:
                return HttpResponseBadRequest("direction must be 'up' or 'down'")
            sibling_qs = PersistentLLMTier.objects.filter(
                token_range=tier.token_range,
                intelligence_tier=tier.intelligence_tier,
            )
            changed = _swap_orders(sibling_qs, tier, direction)
            if not changed:
                return HttpResponseBadRequest("Unable to move tier in that direction")
        tier.save()
        invalidate_llm_bootstrap_cache()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(PersistentLLMTier, pk=tier_id)
        tier.delete()
        invalidate_llm_bootstrap_cache()
        return _json_ok()


class PersistentTierEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(PersistentLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_id = payload.get("endpoint_id")
        endpoint = get_object_or_404(PersistentModelEndpoint, pk=endpoint_id)
        if tier.tier_endpoints.filter(endpoint=endpoint).exists():
            return HttpResponseBadRequest("Endpoint already exists in tier")

        try:
            weight = float(payload.get("weight", 1))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")

        try:
            reasoning_override = _validate_reasoning_override(endpoint, payload.get("reasoning_effort_override"))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        te = PersistentTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            weight=weight,
            reasoning_effort_override=reasoning_override,
        )
        invalidate_llm_bootstrap_cache()
        return _json_ok(tier_endpoint_id=str(te.id))


class PersistentTierEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(PersistentTierEndpoint, pk=tier_endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "weight" in payload:
            try:
                weight = float(payload.get("weight"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("weight must be numeric")
            if weight <= 0:
                return HttpResponseBadRequest("weight must be greater than zero")
            tier_endpoint.weight = weight
        if "reasoning_effort_override" in payload:
            try:
                reasoning_override = _validate_reasoning_override(tier_endpoint.endpoint, payload.get("reasoning_effort_override"))
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
            tier_endpoint.reasoning_effort_override = reasoning_override
        tier_endpoint.save()
        invalidate_llm_bootstrap_cache()
        return _json_ok(tier_endpoint_id=str(tier_endpoint.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(PersistentTierEndpoint, pk=tier_endpoint_id)
        tier_endpoint.delete()
        invalidate_llm_bootstrap_cache()
        return _json_ok()


class BrowserEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        provider = get_object_or_404(LLMProvider, pk=payload.get("provider_id"))
        key = (payload.get("key") or "").strip()
        model = (payload.get("model") or payload.get("browser_model") or "").strip()
        if not key or not model:
            return HttpResponseBadRequest("key and model are required")
        if BrowserModelEndpoint.objects.filter(key=key).exists():
            return HttpResponseBadRequest("Endpoint key already exists")
        if provider.model_prefix and model.startswith(provider.model_prefix):
            return HttpResponseBadRequest("Store browser models without the provider prefix; it is applied at runtime when necessary.")

        max_tokens_val = payload.get("max_output_tokens")
        max_output_tokens = None
        if max_tokens_val not in (None, ""):
            try:
                max_output_tokens = int(max_tokens_val)
            except (TypeError, ValueError):
                return HttpResponseBadRequest("max_output_tokens must be an integer")

        base_url = (payload.get("browser_base_url") or payload.get("api_base") or "").strip()
        if provider.browser_backend == LLMProvider.BrowserBackend.OPENAI_COMPAT and not base_url:
            if provider.key == "openrouter":
                base_url = DEFAULT_API_BASE
            else:
                return HttpResponseBadRequest("Browser API base URL is required for OpenAI-compatible providers.")

        endpoint = BrowserModelEndpoint.objects.create(
            key=key,
            provider=provider,
            browser_model=model,
            browser_base_url=base_url,
            max_output_tokens=max_output_tokens,
            supports_temperature=_coerce_bool(payload.get("supports_temperature", True)),
            supports_vision=_coerce_bool(payload.get("supports_vision", False)),
            low_latency=_coerce_bool(payload.get("low_latency", False)),
            enabled=_coerce_bool(payload.get("enabled", True)),
        )
        return _json_ok(endpoint_id=str(endpoint.id))


class BrowserEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(BrowserModelEndpoint, pk=endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "model" in payload or "browser_model" in payload:
            model = (payload.get("model") or payload.get("browser_model") or "").strip()
            if model:
                provider = endpoint.provider
                if provider and provider.model_prefix and model.startswith(provider.model_prefix):
                    return HttpResponseBadRequest("Store browser models without the provider prefix; it is applied at runtime when necessary.")
                endpoint.browser_model = model
        if "browser_base_url" in payload or "api_base" in payload:
            provider = endpoint.provider
            base_url = (payload.get("browser_base_url") or payload.get("api_base") or "").strip()
            if provider and provider.browser_backend == LLMProvider.BrowserBackend.OPENAI_COMPAT and not base_url:
                if provider.key == "openrouter":
                    base_url = DEFAULT_API_BASE
                else:
                    return HttpResponseBadRequest("Browser API base URL is required for OpenAI-compatible providers.")
            endpoint.browser_base_url = base_url
        if "max_output_tokens" in payload:
            value = payload.get("max_output_tokens")
            if value in (None, ""):
                endpoint.max_output_tokens = None
            else:
                try:
                    endpoint.max_output_tokens = int(value)
                except (TypeError, ValueError):
                    return HttpResponseBadRequest("max_output_tokens must be an integer")
        if "supports_temperature" in payload:
            endpoint.supports_temperature = _coerce_bool(payload.get("supports_temperature"))
        if "supports_vision" in payload:
            endpoint.supports_vision = _coerce_bool(payload.get("supports_vision"))
        if "low_latency" in payload:
            endpoint.low_latency = _coerce_bool(payload.get("low_latency"))
        if "enabled" in payload:
            endpoint.enabled = _coerce_bool(payload.get("enabled"))
        endpoint.save()
        return _json_ok(endpoint_id=str(endpoint.id))

    def delete(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(BrowserModelEndpoint, pk=endpoint_id)
        if endpoint.in_tiers.exists():
            return HttpResponseBadRequest("Remove endpoint from tiers before deleting")
        endpoint.delete()
        return _json_ok()


class EmbeddingEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        key = (payload.get("key") or "").strip()
        model = (payload.get("model") or payload.get("litellm_model") or "").strip()
        if not key or not model:
            return HttpResponseBadRequest("key and model are required")
        if EmbeddingsModelEndpoint.objects.filter(key=key).exists():
            return HttpResponseBadRequest("Endpoint key already exists")

        provider_id = payload.get("provider_id")
        provider = None
        if provider_id:
            provider = get_object_or_404(LLMProvider, pk=provider_id)

        endpoint = EmbeddingsModelEndpoint.objects.create(
            key=key,
            provider=provider,
            litellm_model=model,
            api_base=(payload.get("api_base") or "").strip(),
            low_latency=_coerce_bool(payload.get("low_latency", False)),
            enabled=_coerce_bool(payload.get("enabled", True)),
        )
        return _json_ok(endpoint_id=str(endpoint.id))


class EmbeddingEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(EmbeddingsModelEndpoint, pk=endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "model" in payload or "litellm_model" in payload:
            model = (payload.get("model") or payload.get("litellm_model") or "").strip()
            if model:
                endpoint.litellm_model = model
        if "api_base" in payload:
            endpoint.api_base = (payload.get("api_base") or "").strip()
        if "low_latency" in payload:
            endpoint.low_latency = _coerce_bool(payload.get("low_latency"))
        if "enabled" in payload:
            endpoint.enabled = _coerce_bool(payload.get("enabled"))
        if "provider_id" in payload:
            provider_id = payload.get("provider_id")
            if provider_id:
                endpoint.provider = get_object_or_404(LLMProvider, pk=provider_id)
            else:
                endpoint.provider = None
        endpoint.save()
        return _json_ok(endpoint_id=str(endpoint.id))

    def delete(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(EmbeddingsModelEndpoint, pk=endpoint_id)
        if endpoint.in_tiers.exists():
            return HttpResponseBadRequest("Remove endpoint from tiers before deleting")
        endpoint.delete()
        return _json_ok()


class BrowserTierListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        policy = _get_active_browser_policy()
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        try:
            intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        description = (payload.get("description") or "").strip()
        order = _next_order_for_browser(policy, intelligence_tier)
        tier = BrowserLLMTier.objects.create(
            policy=policy,
            order=order,
            description=description,
            intelligence_tier=intelligence_tier,
        )
        return _json_ok(tier_id=str(tier.id))


class BrowserTierDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(BrowserLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        if "move" in payload:
            direction = (payload.get("move") or "").lower()
            if direction not in {"up", "down"}:
                return HttpResponseBadRequest("direction must be 'up' or 'down'")
            sibling_qs = BrowserLLMTier.objects.filter(policy=tier.policy, intelligence_tier=tier.intelligence_tier)
            changed = _swap_orders(sibling_qs, tier, direction)
            if not changed:
                return HttpResponseBadRequest("Unable to move tier in that direction")
        tier.save()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(BrowserLLMTier, pk=tier_id)
        tier.delete()
        return _json_ok()


class BrowserTierEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(BrowserLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint = get_object_or_404(BrowserModelEndpoint, pk=payload.get("endpoint_id"))
        extraction_endpoint = None
        extraction_endpoint_id = payload.get("extraction_endpoint_id")
        if extraction_endpoint_id:
            extraction_endpoint = get_object_or_404(BrowserModelEndpoint, pk=extraction_endpoint_id)
        if tier.tier_endpoints.filter(endpoint=endpoint).exists():
            return HttpResponseBadRequest("Endpoint already exists in tier")
        try:
            weight = float(payload.get("weight", 1))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")
        te = BrowserTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            extraction_endpoint=extraction_endpoint,
            weight=weight,
        )
        return _json_ok(tier_endpoint_id=str(te.id))


class BrowserTierEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(BrowserTierEndpoint, pk=tier_endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if "weight" in payload:
            try:
                weight = float(payload.get("weight"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("weight must be numeric")
            if weight <= 0:
                return HttpResponseBadRequest("weight must be greater than zero")
            tier_endpoint.weight = weight
        if "extraction_endpoint_id" in payload:
            extraction_endpoint_id = payload.get("extraction_endpoint_id")
            extraction_endpoint = None
            if extraction_endpoint_id:
                extraction_endpoint = get_object_or_404(BrowserModelEndpoint, pk=extraction_endpoint_id)
            tier_endpoint.extraction_endpoint = extraction_endpoint
        tier_endpoint.save()
        return _json_ok(tier_endpoint_id=str(tier_endpoint.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(BrowserTierEndpoint, pk=tier_endpoint_id)
        tier_endpoint.delete()
        return _json_ok()


class EmbeddingTierListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        description = (payload.get("description") or "").strip()
        order = _next_embedding_order()
        tier = EmbeddingsLLMTier.objects.create(order=order, description=description)
        return _json_ok(tier_id=str(tier.id))


class EmbeddingTierDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(EmbeddingsLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        if "move" in payload:
            direction = (payload.get("move") or "").lower()
            if direction not in {"up", "down"}:
                return HttpResponseBadRequest("direction must be 'up' or 'down'")
            changed = _swap_orders(EmbeddingsLLMTier.objects.all(), tier, direction)
            if not changed:
                return HttpResponseBadRequest("Unable to move tier in that direction")
        tier.save()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(EmbeddingsLLMTier, pk=tier_id)
        tier.delete()
        return _json_ok()


class EmbeddingTierEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(EmbeddingsLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint = get_object_or_404(EmbeddingsModelEndpoint, pk=payload.get("endpoint_id"))
        if tier.tier_endpoints.filter(endpoint=endpoint).exists():
            return HttpResponseBadRequest("Endpoint already exists in tier")
        try:
            weight = float(payload.get("weight", 1))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")
        te = EmbeddingsTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=weight)
        return _json_ok(tier_endpoint_id=str(te.id))


class EmbeddingTierEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(EmbeddingsTierEndpoint, pk=tier_endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if "weight" in payload:
            try:
                weight = float(payload.get("weight"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("weight must be numeric")
            if weight <= 0:
                return HttpResponseBadRequest("weight must be greater than zero")
            tier_endpoint.weight = weight
        tier_endpoint.save()
        return _json_ok(tier_endpoint_id=str(tier_endpoint.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(EmbeddingsTierEndpoint, pk=tier_endpoint_id)
        tier_endpoint.delete()
        return _json_ok()


class FileHandlerEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        key = (payload.get("key") or "").strip()
        model = (payload.get("model") or payload.get("litellm_model") or "").strip()
        if not key or not model:
            return HttpResponseBadRequest("key and model are required")
        if FileHandlerModelEndpoint.objects.filter(key=key).exists():
            return HttpResponseBadRequest("Endpoint key already exists")

        provider_id = payload.get("provider_id")
        provider = None
        if provider_id:
            provider = get_object_or_404(LLMProvider, pk=provider_id)

        endpoint = FileHandlerModelEndpoint.objects.create(
            key=key,
            provider=provider,
            litellm_model=model,
            api_base=(payload.get("api_base") or "").strip(),
            supports_vision=_coerce_bool(payload.get("supports_vision", False)),
            low_latency=_coerce_bool(payload.get("low_latency", False)),
            enabled=_coerce_bool(payload.get("enabled", True)),
        )
        return _json_ok(endpoint_id=str(endpoint.id))


class FileHandlerEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(FileHandlerModelEndpoint, pk=endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "model" in payload or "litellm_model" in payload:
            model = (payload.get("model") or payload.get("litellm_model") or "").strip()
            if model:
                endpoint.litellm_model = model
        if "api_base" in payload:
            endpoint.api_base = (payload.get("api_base") or "").strip()
        if "supports_vision" in payload:
            endpoint.supports_vision = _coerce_bool(payload.get("supports_vision"))
        if "low_latency" in payload:
            endpoint.low_latency = _coerce_bool(payload.get("low_latency"))
        if "enabled" in payload:
            endpoint.enabled = _coerce_bool(payload.get("enabled"))
        if "provider_id" in payload:
            provider_id = payload.get("provider_id")
            if provider_id:
                endpoint.provider = get_object_or_404(LLMProvider, pk=provider_id)
            else:
                endpoint.provider = None
        endpoint.save()
        return _json_ok(endpoint_id=str(endpoint.id))

    def delete(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(FileHandlerModelEndpoint, pk=endpoint_id)
        if endpoint.in_tiers.exists():
            return HttpResponseBadRequest("Remove endpoint from tiers before deleting")
        endpoint.delete()
        return _json_ok()


class FileHandlerTierListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        description = (payload.get("description") or "").strip()
        order = _next_file_handler_order()
        tier = FileHandlerLLMTier.objects.create(order=order, description=description)
        return _json_ok(tier_id=str(tier.id))


class FileHandlerTierDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(FileHandlerLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        if "move" in payload:
            direction = (payload.get("move") or "").lower()
            if direction not in {"up", "down"}:
                return HttpResponseBadRequest("direction must be 'up' or 'down'")
            changed = _swap_orders(FileHandlerLLMTier.objects.all(), tier, direction)
            if not changed:
                return HttpResponseBadRequest("Unable to move tier in that direction")
        tier.save()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(FileHandlerLLMTier, pk=tier_id)
        tier.delete()
        return _json_ok()


class FileHandlerTierEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(FileHandlerLLMTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint = get_object_or_404(FileHandlerModelEndpoint, pk=payload.get("endpoint_id"))
        if tier.tier_endpoints.filter(endpoint=endpoint).exists():
            return HttpResponseBadRequest("Endpoint already exists in tier")
        try:
            weight = float(payload.get("weight", 1))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")
        te = FileHandlerTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=weight)
        return _json_ok(tier_endpoint_id=str(te.id))


class FileHandlerTierEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(FileHandlerTierEndpoint, pk=tier_endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if "weight" in payload:
            try:
                weight = float(payload.get("weight"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("weight must be numeric")
            if weight <= 0:
                return HttpResponseBadRequest("weight must be greater than zero")
            tier_endpoint.weight = weight
        tier_endpoint.save()
        return _json_ok(tier_endpoint_id=str(tier_endpoint.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(FileHandlerTierEndpoint, pk=tier_endpoint_id)
        tier_endpoint.delete()
        return _json_ok()


# =============================================================================
# LLM Routing Profile APIs
# =============================================================================

class LLMRoutingProfileListCreateAPIView(SystemAdminAPIView):
    """List all routing profiles or create a new one."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        from console.llm_serializers import build_routing_profiles_list
        profiles = build_routing_profiles_list()
        return JsonResponse({"profiles": profiles})

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        name = (payload.get("name") or "").strip()
        display_name = (payload.get("display_name") or "").strip()
        if not name:
            return HttpResponseBadRequest("name is required")
        if not display_name:
            display_name = name

        if LLMRoutingProfile.objects.filter(name=name).exists():
            return HttpResponseBadRequest("A profile with that name already exists")

        profile = LLMRoutingProfile.objects.create(
            name=name,
            display_name=display_name,
            description=(payload.get("description") or "").strip(),
            is_active=False,  # Never create as active by default
            created_by=request.user,
        )
        return _json_ok(profile_id=str(profile.id))


class LLMRoutingProfileDetailAPIView(SystemAdminAPIView):
    """Get, update, or delete a specific routing profile."""
    http_method_names = ["get", "patch", "delete"]

    def get(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile
        from console.llm_serializers import get_routing_profile_with_prefetch, serialize_routing_profile_detail
        try:
            profile = get_routing_profile_with_prefetch(profile_id)
        except LLMRoutingProfile.DoesNotExist:
            return JsonResponse({"error": "Profile not found"}, status=404)
        return JsonResponse({"profile": serialize_routing_profile_detail(profile)})

    def patch(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile, PersistentModelEndpoint
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "display_name" in payload:
            profile.display_name = (payload.get("display_name") or "").strip()
        if "description" in payload:
            profile.description = (payload.get("description") or "").strip()

        # Name changes require uniqueness check
        if "name" in payload:
            new_name = (payload.get("name") or "").strip()
            if new_name and new_name != profile.name:
                if LLMRoutingProfile.objects.filter(name=new_name).exclude(pk=profile.id).exists():
                    return HttpResponseBadRequest("A profile with that name already exists")
                profile.name = new_name

        # Eval judge endpoint update
        if "eval_judge_endpoint_id" in payload:
            endpoint_id = payload.get("eval_judge_endpoint_id")
            if endpoint_id is None or endpoint_id == "":
                profile.eval_judge_endpoint = None
            else:
                try:
                    endpoint = PersistentModelEndpoint.objects.get(pk=endpoint_id)
                    profile.eval_judge_endpoint = endpoint
                except PersistentModelEndpoint.DoesNotExist:
                    return HttpResponseBadRequest("Invalid eval judge endpoint ID")

        profile.save()
        return _json_ok(profile_id=str(profile.id))

    def delete(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        if profile.is_active:
            return HttpResponseBadRequest("Cannot delete the active routing profile")
        profile.delete()
        return _json_ok()


class LLMRoutingProfileActivateAPIView(SystemAdminAPIView):
    """Activate a specific routing profile (deactivates others)."""
    http_method_names = ["post"]

    def post(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)

        with transaction.atomic():
            # Deactivate all other profiles
            LLMRoutingProfile.objects.exclude(pk=profile.id).update(is_active=False)
            # Activate this one
            profile.is_active = True
            profile.save(update_fields=["is_active", "updated_at"])

        invalidate_llm_bootstrap_cache()
        return _json_ok(profile_id=str(profile.id))


class LLMRoutingProfileCloneAPIView(SystemAdminAPIView):
    """Clone a routing profile with all its nested configuration."""
    http_method_names = ["post"]

    def post(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import (
            LLMRoutingProfile,
            ProfileTokenRange,
            ProfilePersistentTier,
            ProfilePersistentTierEndpoint,
            ProfileBrowserTier,
            ProfileBrowserTierEndpoint,
            ProfileEmbeddingsTier,
            ProfileEmbeddingsTierEndpoint,
        )
        from console.llm_serializers import get_routing_profile_with_prefetch

        try:
            source = get_routing_profile_with_prefetch(profile_id)
        except LLMRoutingProfile.DoesNotExist:
            return JsonResponse({"error": "Profile not found"}, status=404)

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        # Generate a unique name for the clone
        base_name = (payload.get("name") or "").strip()
        if not base_name:
            base_name = f"{source.name}-copy"
        name = base_name
        counter = 1
        while LLMRoutingProfile.objects.filter(name=name).exists():
            counter += 1
            name = f"{base_name}-{counter}"

        display_name = (payload.get("display_name") or "").strip()
        if not display_name:
            display_name = f"{source.display_name} (Copy)"

        with transaction.atomic():
            # Create the new profile
            clone = LLMRoutingProfile.objects.create(
                name=name,
                display_name=display_name,
                description=payload.get("description") or source.description,
                is_active=False,
                created_by=request.user,
                cloned_from=source,
                eval_judge_endpoint=source.eval_judge_endpoint,
            )

            # Clone persistent config: token ranges -> tiers -> endpoints
            for src_range in source.persistent_token_ranges.all():
                new_range = ProfileTokenRange.objects.create(
                    profile=clone,
                    name=src_range.name,
                    min_tokens=src_range.min_tokens,
                    max_tokens=src_range.max_tokens,
                )
                for src_tier in src_range.tiers.all():
                    new_tier = ProfilePersistentTier.objects.create(
                        token_range=new_range,
                        order=src_tier.order,
                        description=src_tier.description,
                        intelligence_tier=src_tier.intelligence_tier,
                    )
                    for src_te in src_tier.tier_endpoints.all():
                        ProfilePersistentTierEndpoint.objects.create(
                            tier=new_tier,
                            endpoint=src_te.endpoint,
                            weight=src_te.weight,
                            reasoning_effort_override=getattr(src_te, "reasoning_effort_override", None),
                        )

            # Clone browser config: tiers -> endpoints
            for src_tier in source.browser_tiers.all():
                new_tier = ProfileBrowserTier.objects.create(
                    profile=clone,
                    order=src_tier.order,
                    description=src_tier.description,
                    intelligence_tier=src_tier.intelligence_tier,
                )
                for src_te in src_tier.tier_endpoints.all():
                    ProfileBrowserTierEndpoint.objects.create(
                        tier=new_tier,
                        endpoint=src_te.endpoint,
                        weight=src_te.weight,
                    )

            # Clone embeddings config: tiers -> endpoints
            for src_tier in source.embeddings_tiers.all():
                new_tier = ProfileEmbeddingsTier.objects.create(
                    profile=clone,
                    order=src_tier.order,
                    description=src_tier.description,
                )
                for src_te in src_tier.tier_endpoints.all():
                    ProfileEmbeddingsTierEndpoint.objects.create(
                        tier=new_tier,
                        endpoint=src_te.endpoint,
                        weight=src_te.weight,
                    )

        return _json_ok(profile_id=str(clone.id), name=clone.name)


# Profile nested config management (token ranges, tiers, tier endpoints)

class ProfileTokenRangeListCreateAPIView(SystemAdminAPIView):
    """List or create token ranges for a profile."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile, ProfileTokenRange
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        ranges = ProfileTokenRange.objects.filter(profile=profile).order_by("min_tokens")
        payload = [{
            "id": str(r.id),
            "name": r.name,
            "min_tokens": r.min_tokens,
            "max_tokens": r.max_tokens,
        } for r in ranges]
        return JsonResponse({"ranges": payload})

    def post(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile, ProfileTokenRange
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        name = (payload.get("name") or "").strip()
        min_tokens = payload.get("min_tokens", 0)
        max_tokens = payload.get("max_tokens")

        if not name:
            return HttpResponseBadRequest("name is required")

        token_range = ProfileTokenRange.objects.create(
            profile=profile,
            name=name,
            min_tokens=min_tokens,
            max_tokens=max_tokens,
        )
        return _json_ok(range_id=str(token_range.id))


class ProfileTokenRangeDetailAPIView(SystemAdminAPIView):
    """Update or delete a profile token range."""
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileTokenRange
        token_range = get_object_or_404(ProfileTokenRange, pk=range_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "name" in payload:
            token_range.name = (payload.get("name") or "").strip()
        if "min_tokens" in payload:
            token_range.min_tokens = payload.get("min_tokens", 0)
        if "max_tokens" in payload:
            token_range.max_tokens = payload.get("max_tokens")

        token_range.save()
        return _json_ok(range_id=str(token_range.id))

    def delete(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileTokenRange
        token_range = get_object_or_404(ProfileTokenRange, pk=range_id)
        token_range.delete()
        return _json_ok()


class ProfilePersistentTierListCreateAPIView(SystemAdminAPIView):
    """List or create tiers for a profile token range."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileTokenRange, ProfilePersistentTier
        token_range = get_object_or_404(ProfileTokenRange, pk=range_id)
        tiers = ProfilePersistentTier.objects.filter(token_range=token_range).order_by("intelligence_tier__rank", "order")
        payload = [{
            "id": str(t.id),
            "order": t.order,
            "description": t.description,
            "intelligence_tier": {
                "key": t.intelligence_tier.key,
                "display_name": t.intelligence_tier.display_name,
                "rank": t.intelligence_tier.rank,
                "credit_multiplier": str(t.intelligence_tier.credit_multiplier),
            },
        } for t in tiers]
        return JsonResponse({"tiers": payload})

    def post(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileTokenRange, ProfilePersistentTier
        token_range = get_object_or_404(ProfileTokenRange, pk=range_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        order = payload.get("order", 0)
        try:
            intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        create_kwargs: dict[str, Any] = {
            "token_range": token_range,
            "order": order,
            "description": (payload.get("description") or "").strip(),
            "intelligence_tier": intelligence_tier,
        }

        tier = ProfilePersistentTier.objects.create(**create_kwargs)
        return _json_ok(tier_id=str(tier.id))


class ProfilePersistentTierDetailAPIView(SystemAdminAPIView):
    """Update or delete a profile persistent tier."""
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfilePersistentTier
        tier = get_object_or_404(ProfilePersistentTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "move" in payload:
            direction = (payload.get("move") or "").lower()
            if direction not in {"up", "down"}:
                return HttpResponseBadRequest("direction must be 'up' or 'down'")
            siblings = ProfilePersistentTier.objects.filter(
                token_range=tier.token_range,
                intelligence_tier=tier.intelligence_tier,
            )
            changed = _swap_orders(siblings, tier, direction)
            if not changed:
                return HttpResponseBadRequest("Unable to move tier in that direction")

        if "order" in payload:
            tier.order = payload.get("order", 0)
        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        if "intelligence_tier" in payload or "is_premium" in payload or "is_max" in payload:
            try:
                tier.intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))

        tier.save()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfilePersistentTier
        tier = get_object_or_404(ProfilePersistentTier, pk=tier_id)
        tier.delete()
        return _json_ok()


class ProfilePersistentTierEndpointListCreateAPIView(SystemAdminAPIView):
    """List or create endpoints for a profile persistent tier."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfilePersistentTier, ProfilePersistentTierEndpoint
        tier = get_object_or_404(ProfilePersistentTier, pk=tier_id)
        endpoints = ProfilePersistentTierEndpoint.objects.filter(tier=tier).select_related("endpoint__provider")
        payload = [{
            "id": str(te.id),
            "endpoint_id": str(te.endpoint_id),
            "label": f"{te.endpoint.provider.display_name} · {te.endpoint.litellm_model}",
            "weight": float(te.weight),
            "reasoning_effort_override": te.reasoning_effort_override,
            "supports_reasoning": te.endpoint.supports_reasoning,
            "endpoint_reasoning_effort": te.endpoint.reasoning_effort,
        } for te in endpoints]
        return JsonResponse({"endpoints": payload})

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfilePersistentTier, ProfilePersistentTierEndpoint
        tier = get_object_or_404(ProfilePersistentTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_id = payload.get("endpoint_id")
        if not endpoint_id:
            return HttpResponseBadRequest("endpoint_id is required")
        endpoint = get_object_or_404(PersistentModelEndpoint, pk=endpoint_id)

        try:
            weight = float(payload.get("weight", 1.0))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")

        try:
            reasoning_override = _validate_reasoning_override(endpoint, payload.get("reasoning_effort_override"))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        te = ProfilePersistentTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            weight=weight,
            reasoning_effort_override=reasoning_override,
        )
        return _json_ok(tier_endpoint_id=str(te.id))


class ProfilePersistentTierEndpointDetailAPIView(SystemAdminAPIView):
    """Update or delete a profile persistent tier endpoint."""
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfilePersistentTierEndpoint
        te = get_object_or_404(ProfilePersistentTierEndpoint, pk=tier_endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "weight" in payload:
            try:
                weight = float(payload.get("weight"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("weight must be numeric")
            if weight <= 0:
                return HttpResponseBadRequest("weight must be greater than zero")
            te.weight = weight
        if "reasoning_effort_override" in payload:
            try:
                reasoning_override = _validate_reasoning_override(te.endpoint, payload.get("reasoning_effort_override"))
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
            te.reasoning_effort_override = reasoning_override
        te.save()
        return _json_ok(tier_endpoint_id=str(te.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfilePersistentTierEndpoint
        te = get_object_or_404(ProfilePersistentTierEndpoint, pk=tier_endpoint_id)
        te.delete()
        return _json_ok()


# Profile browser tier management

class ProfileBrowserTierListCreateAPIView(SystemAdminAPIView):
    """List or create browser tiers for a profile."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile, ProfileBrowserTier
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        tiers = ProfileBrowserTier.objects.filter(profile=profile).order_by("intelligence_tier__rank", "order")
        payload = [{
            "id": str(t.id),
            "order": t.order,
            "description": t.description,
            "intelligence_tier": {
                "key": t.intelligence_tier.key,
                "display_name": t.intelligence_tier.display_name,
                "rank": t.intelligence_tier.rank,
                "credit_multiplier": str(t.intelligence_tier.credit_multiplier),
            },
        } for t in tiers]
        return JsonResponse({"tiers": payload})

    def post(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile, ProfileBrowserTier
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        raw_order = payload.get("order")
        try:
            order = int(raw_order) if raw_order is not None else None
        except (TypeError, ValueError):
            return HttpResponseBadRequest("order must be an integer")

        try:
            intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if order is None or order <= 0:
            max_order = (
                ProfileBrowserTier.objects.filter(profile=profile, intelligence_tier=intelligence_tier)
                .aggregate(max_order=Max("order"))
                .get("max_order")
                or 0
            )
            order = max_order + 1
        elif ProfileBrowserTier.objects.filter(profile=profile, intelligence_tier=intelligence_tier, order=order).exists():
            # Append to the end if the requested order is already taken to avoid unique constraint errors
            max_order = (
                ProfileBrowserTier.objects.filter(profile=profile, intelligence_tier=intelligence_tier)
                .aggregate(max_order=Max("order"))
                .get("max_order")
                or 0
            )
            order = max_order + 1

        tier = ProfileBrowserTier.objects.create(
            profile=profile,
            order=order,
            description=(payload.get("description") or "").strip(),
            intelligence_tier=intelligence_tier,
        )
        return _json_ok(tier_id=str(tier.id))


class ProfileBrowserTierDetailAPIView(SystemAdminAPIView):
    """Update or delete a profile browser tier."""
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileBrowserTier
        tier = get_object_or_404(ProfileBrowserTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "move" in payload:
            direction = (payload.get("move") or "").lower()
            if direction not in {"up", "down"}:
                return HttpResponseBadRequest("direction must be 'up' or 'down'")
            siblings = ProfileBrowserTier.objects.filter(
                profile=tier.profile,
                intelligence_tier=tier.intelligence_tier,
            )
            changed = _swap_orders(siblings, tier, direction)
            if not changed:
                return HttpResponseBadRequest("Unable to move tier in that direction")

        if "order" in payload:
            tier.order = payload.get("order", 0)
        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        if "intelligence_tier" in payload or "is_premium" in payload or "is_max" in payload:
            try:
                tier.intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
        tier.save()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileBrowserTier
        tier = get_object_or_404(ProfileBrowserTier, pk=tier_id)
        tier.delete()
        return _json_ok()


class ProfileBrowserTierEndpointListCreateAPIView(SystemAdminAPIView):
    """List or create endpoints for a profile browser tier."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileBrowserTier, ProfileBrowserTierEndpoint
        tier = get_object_or_404(ProfileBrowserTier, pk=tier_id)
        endpoints = ProfileBrowserTierEndpoint.objects.filter(tier=tier).select_related(
            "endpoint__provider",
            "extraction_endpoint__provider",
        )
        payload = [{
            "id": str(te.id),
            "endpoint_id": str(te.endpoint_id),
            "label": f"{te.endpoint.provider.display_name} · {te.endpoint.browser_model}",
            "weight": float(te.weight),
            "extraction_endpoint_id": str(te.extraction_endpoint_id) if te.extraction_endpoint_id else None,
            "extraction_label": (
                f"{te.extraction_endpoint.provider.display_name} · {te.extraction_endpoint.browser_model}"
                if te.extraction_endpoint
                else None
            ),
        } for te in endpoints]
        return JsonResponse({"endpoints": payload})

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileBrowserTier, ProfileBrowserTierEndpoint
        tier = get_object_or_404(ProfileBrowserTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_id = payload.get("endpoint_id")
        if not endpoint_id:
            return HttpResponseBadRequest("endpoint_id is required")
        endpoint = get_object_or_404(BrowserModelEndpoint, pk=endpoint_id)
        extraction_endpoint = None
        extraction_endpoint_id = payload.get("extraction_endpoint_id")
        if extraction_endpoint_id:
            extraction_endpoint = get_object_or_404(BrowserModelEndpoint, pk=extraction_endpoint_id)

        try:
            weight = float(payload.get("weight", 1.0))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")

        te = ProfileBrowserTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            extraction_endpoint=extraction_endpoint,
            weight=weight,
        )
        return _json_ok(tier_endpoint_id=str(te.id))


class ProfileBrowserTierEndpointDetailAPIView(SystemAdminAPIView):
    """Update or delete a profile browser tier endpoint."""
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileBrowserTierEndpoint
        te = get_object_or_404(ProfileBrowserTierEndpoint, pk=tier_endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "weight" in payload:
            try:
                weight = float(payload.get("weight"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("weight must be numeric")
            if weight <= 0:
                return HttpResponseBadRequest("weight must be greater than zero")
            te.weight = weight
        if "extraction_endpoint_id" in payload:
            extraction_endpoint_id = payload.get("extraction_endpoint_id")
            extraction_endpoint = None
            if extraction_endpoint_id:
                extraction_endpoint = get_object_or_404(BrowserModelEndpoint, pk=extraction_endpoint_id)
            te.extraction_endpoint = extraction_endpoint
        te.save()
        return _json_ok(tier_endpoint_id=str(te.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileBrowserTierEndpoint
        te = get_object_or_404(ProfileBrowserTierEndpoint, pk=tier_endpoint_id)
        te.delete()
        return _json_ok()


# Profile embeddings tier management

class ProfileEmbeddingsTierListCreateAPIView(SystemAdminAPIView):
    """List or create embeddings tiers for a profile."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile, ProfileEmbeddingsTier
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        tiers = ProfileEmbeddingsTier.objects.filter(profile=profile).order_by("order")
        payload = [{
            "id": str(t.id),
            "order": t.order,
            "description": t.description,
        } for t in tiers]
        return JsonResponse({"tiers": payload})

    def post(self, request: HttpRequest, profile_id: str, *args: Any, **kwargs: Any):
        from api.models import LLMRoutingProfile, ProfileEmbeddingsTier
        profile = get_object_or_404(LLMRoutingProfile, pk=profile_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        tier = ProfileEmbeddingsTier.objects.create(
            profile=profile,
            order=payload.get("order", 0),
            description=(payload.get("description") or "").strip(),
        )
        return _json_ok(tier_id=str(tier.id))


class ProfileEmbeddingsTierDetailAPIView(SystemAdminAPIView):
    """Update or delete a profile embeddings tier."""
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileEmbeddingsTier
        tier = get_object_or_404(ProfileEmbeddingsTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "order" in payload:
            tier.order = payload.get("order", 0)
        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        tier.save()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileEmbeddingsTier
        tier = get_object_or_404(ProfileEmbeddingsTier, pk=tier_id)
        tier.delete()
        return _json_ok()


class ProfileEmbeddingsTierEndpointListCreateAPIView(SystemAdminAPIView):
    """List or create endpoints for a profile embeddings tier."""
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileEmbeddingsTier, ProfileEmbeddingsTierEndpoint
        tier = get_object_or_404(ProfileEmbeddingsTier, pk=tier_id)
        endpoints = ProfileEmbeddingsTierEndpoint.objects.filter(tier=tier).select_related("endpoint__provider")
        payload = [{
            "id": str(te.id),
            "endpoint_id": str(te.endpoint_id),
            "label": f"{te.endpoint.provider.display_name if te.endpoint.provider else 'Unlinked'} · {te.endpoint.litellm_model}",
            "weight": float(te.weight),
        } for te in endpoints]
        return JsonResponse({"endpoints": payload})

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileEmbeddingsTier, ProfileEmbeddingsTierEndpoint
        tier = get_object_or_404(ProfileEmbeddingsTier, pk=tier_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_id = payload.get("endpoint_id")
        if not endpoint_id:
            return HttpResponseBadRequest("endpoint_id is required")
        endpoint = get_object_or_404(EmbeddingsModelEndpoint, pk=endpoint_id)

        try:
            weight = float(payload.get("weight", 1.0))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")

        te = ProfileEmbeddingsTierEndpoint.objects.create(tier=tier, endpoint=endpoint, weight=weight)
        return _json_ok(tier_endpoint_id=str(te.id))


class ProfileEmbeddingsTierEndpointDetailAPIView(SystemAdminAPIView):
    """Update or delete a profile embeddings tier endpoint."""
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileEmbeddingsTierEndpoint
        te = get_object_or_404(ProfileEmbeddingsTierEndpoint, pk=tier_endpoint_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "weight" in payload:
            try:
                weight = float(payload.get("weight"))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("weight must be numeric")
            if weight <= 0:
                return HttpResponseBadRequest("weight must be greater than zero")
            te.weight = weight
        te.save()
        return _json_ok(tier_endpoint_id=str(te.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        from api.models import ProfileEmbeddingsTierEndpoint
        te = get_object_or_404(ProfileEmbeddingsTierEndpoint, pk=tier_endpoint_id)
        te.delete()
        return _json_ok()


@method_decorator(csrf_exempt, name="dispatch")
class AgentProcessingStatusAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        snapshot = build_processing_snapshot(agent)
        return JsonResponse(
            {
                "processing_active": snapshot.active,
                "processing_snapshot": serialize_processing_snapshot(snapshot),
            }
        )


class AgentDailyCreditsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        context = build_agent_daily_credit_context(agent)
        return JsonResponse(
            {
                "dailyCredits": serialize_daily_credit_payload(context),
                "status": build_daily_credit_status(context),
            }
        )

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        owner = agent.organization or agent.user
        credit_settings = get_daily_credit_settings_for_owner(owner)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        new_daily_limit, error = parse_daily_credit_limit(
            payload,
            credit_settings,
            tier_multiplier=get_agent_credit_multiplier(agent),
        )
        if error:
            return JsonResponse({"error": error}, status=400)

        if agent.daily_credit_limit != new_daily_limit:
            agent.daily_credit_limit = new_daily_limit
            agent.save(update_fields=["daily_credit_limit"])

        context = build_agent_daily_credit_context(agent, owner)
        return JsonResponse(
            {
                "dailyCredits": serialize_daily_credit_payload(context),
                "status": build_daily_credit_status(context),
            }
        )


class AgentQuickSettingsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        payload = build_agent_quick_settings_payload(agent)
        return JsonResponse(payload)

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        owner = agent.organization or agent.user
        credit_settings = get_daily_credit_settings_for_owner(owner)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        daily_payload = payload.get("dailyCredits")
        if daily_payload is not None:
            if not isinstance(daily_payload, dict):
                return HttpResponseBadRequest("dailyCredits must be an object")
            new_daily_limit, error = parse_daily_credit_limit(
                daily_payload,
                credit_settings,
                tier_multiplier=get_agent_credit_multiplier(agent),
            )
            if error:
                return JsonResponse({"error": error}, status=400)
            if agent.daily_credit_limit != new_daily_limit:
                agent.daily_credit_limit = new_daily_limit
                agent.save(update_fields=["daily_credit_limit"])

        payload = build_agent_quick_settings_payload(agent, owner)
        return JsonResponse(payload)


class AgentAddonsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    @staticmethod
    def _resolve_agent_addons_context(request: HttpRequest, agent_id: str):
        agent = resolve_agent_for_request(request, agent_id)
        owner = agent.organization or agent.user
        plan_payload = get_organization_plan(agent.organization) if agent.organization_id else get_user_plan(agent.user)
        can_manage_billing = _can_manage_contact_packs(request, agent, plan_payload)
        return agent, owner, plan_payload, can_manage_billing

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent, owner, _, can_manage_billing = self._resolve_agent_addons_context(request, agent_id)
        payload = build_agent_addons_payload(agent, owner, can_manage_billing=can_manage_billing)
        return JsonResponse(payload)

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent, owner, plan_payload, can_manage_billing = self._resolve_agent_addons_context(request, agent_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        contact_pack_payload = payload.get("contactPacks")
        task_pack_payload = payload.get("taskPacks")
        if contact_pack_payload is None and task_pack_payload is None:
            return HttpResponseBadRequest("contactPacks or taskPacks payload is required")
        if not can_manage_billing:
            return JsonResponse({"error": "You do not have permission to manage add-on packs."}, status=403)

        def _validate_pack_payload(pack_payload: object, label: str) -> dict | HttpResponseBadRequest:
            if not isinstance(pack_payload, dict):
                return HttpResponseBadRequest(f"{label} must be an object")
            quantities = pack_payload.get("quantities")
            if not isinstance(quantities, dict):
                return HttpResponseBadRequest(f"{label}.quantities must be an object")
            return quantities

        packs_to_process = [
            ("contactPacks", contact_pack_payload, update_contact_pack_quantities),
            ("taskPacks", task_pack_payload, update_task_pack_quantities),
        ]
        owner_type = "organization" if agent.organization_id else "user"
        plan_id = (plan_payload or {}).get("id")

        for label, pack_payload, update_func in packs_to_process:
            if pack_payload is None:
                continue
            quantities = _validate_pack_payload(pack_payload, label)
            if isinstance(quantities, HttpResponseBadRequest):
                return quantities
            success, error, status = update_func(
                owner=owner,
                owner_type=owner_type,
                plan_id=plan_id,
                quantities=quantities,
            )
            if not success:
                return JsonResponse({"error": error}, status=status)

        payload = build_agent_addons_payload(agent, owner, can_manage_billing=can_manage_billing)
        return JsonResponse(payload)


class MCPServerListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        owner_scope, owner_label, owner_user, owner_org = _resolve_mcp_owner(request)
        queryset = list(_owner_queryset(owner_scope, owner_user, owner_org))
        pending_servers: set[str] = set()
        if request.user.is_authenticated and queryset:
            server_ids = [server.id for server in queryset]
            pending_servers = {
                str(server_id)
                for server_id in MCPServerOAuthSession.objects.filter(
                    server_config_id__in=server_ids,
                    initiated_by=request.user,
                    expires_at__gt=timezone.now(),
                ).values_list("server_config_id", flat=True)
            }
        servers = [_serialize_mcp_server(server, request=request, pending_servers=pending_servers) for server in queryset]
        return JsonResponse(
            {
                "owner_scope": owner_scope,
                "owner_label": owner_label,
                "result_count": len(servers),
                "servers": servers,
            }
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        owner_scope, _, owner_user, owner_org = _resolve_mcp_owner(request)
        allow_commands = flag_is_active(request, SANDBOX_COMPUTE_WAFFLE_FLAG)
        form = MCPServerConfigForm(payload, allow_commands=allow_commands)
        if form.is_valid():
            try:
                with transaction.atomic():
                    server = form.save(user=owner_user, organization=owner_org)
            except IntegrityError:
                form.add_error("name", "A server with that identifier already exists.")
            else:
                manager = get_mcp_manager()
                manager.refresh_server(str(server.id))
                _track_org_event_for_console(
                    request,
                    AnalyticsEvent.MCP_SERVER_CREATED,
                    _mcp_server_event_properties(request, server, owner_scope),
                    organization=owner_org,
                )
                return JsonResponse(
                    {
                        "server": _serialize_mcp_server_detail(server, request),
                        "message": "MCP server saved.",
                    },
                    status=201,
                )

        return JsonResponse({"errors": _form_errors(form)}, status=400)


class MCPServerDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "patch", "delete"]

    def get(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        return JsonResponse({"server": _serialize_mcp_server_detail(server, request)})

    def patch(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        allow_commands = flag_is_active(request, SANDBOX_COMPUTE_WAFFLE_FLAG)
        form = MCPServerConfigForm(payload, instance=server, allow_commands=allow_commands)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save()
            except IntegrityError:
                form.add_error("name", "A server with that identifier already exists.")
            else:
                get_mcp_manager().refresh_server(str(updated.id))
                _track_org_event_for_console(
                    request,
                    AnalyticsEvent.MCP_SERVER_UPDATED,
                    _mcp_server_event_properties(request, updated, updated.scope),
                    organization=updated.organization,
                )
                return JsonResponse({
                    "server": _serialize_mcp_server_detail(updated, request),
                    "message": "MCP server updated.",
                })

        return JsonResponse({"errors": _form_errors(form)}, status=400)

    def delete(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        server_name = server.display_name
        organization = server.organization
        props = _mcp_server_event_properties(request, server, server.scope)
        cached_server_id = str(server.id)
        server.delete()
        get_mcp_manager().remove_server(cached_server_id)
        _track_org_event_for_console(
            request,
            AnalyticsEvent.MCP_SERVER_DELETED,
            props,
            organization=organization,
        )
        return JsonResponse({"message": f"MCP server '{server_name}' was deleted."})


class MCPOAuthStartView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        config_id = body.get("server_config_id")
        if not config_id:
            return HttpResponseBadRequest("server_config_id is required")

        config = _resolve_mcp_server_config(request, str(config_id))
        if config.auth_method != MCPServerConfig.AuthMethod.OAUTH2:
            return HttpResponseBadRequest("This MCP server is not configured for OAuth 2.0.")

        metadata = body.get("metadata") or {}
        if metadata and not isinstance(metadata, dict):
            return HttpResponseBadRequest("metadata must be a JSON object")

        scope_raw = body.get("scope") or ""
        if isinstance(scope_raw, list):
            scope = " ".join(str(part) for part in scope_raw if part)
        else:
            scope = str(scope_raw)

        expires_at = timezone.now() + timedelta(minutes=10)
        state = str(body.get("state") or secrets.token_urlsafe(32))

        callback_url = body.get("redirect_uri") or request.build_absolute_uri(reverse("console-mcp-oauth-callback-view"))

        manual_client_id = str(body.get("client_id") or "")
        manual_client_secret = str(body.get("client_secret") or "")
        client_id = manual_client_id
        client_secret = manual_client_secret

        if not client_id and metadata.get("registration_endpoint"):
            try:
                client_id, client_secret = self._register_dynamic_client(
                    request,
                    metadata,
                    callback_url,
                    config,
                )
            except ValueError as exc:
                return JsonResponse({"error": str(exc)}, status=400)
            except httpx.HTTPError as exc:
                return JsonResponse(
                    {"error": "Client registration failed", "detail": str(exc)},
                    status=502,
                )

        session = MCPServerOAuthSession(
            server_config=config,
            initiated_by=request.user,
            organization=config.organization if config.organization_id else None,
            user=config.user if config.scope == MCPServerConfig.Scope.USER else None,
            state=state,
            redirect_uri=callback_url,
            scope=scope,
            code_challenge=str(body.get("code_challenge") or ""),
            code_challenge_method=str(body.get("code_challenge_method") or ""),
            token_endpoint=str(body.get("token_endpoint") or ""),
            client_id=client_id,
            metadata=metadata,
            expires_at=expires_at,
        )

        code_verifier = body.get("code_verifier")
        if code_verifier:
            session.code_verifier = str(code_verifier)

        if client_secret:
            session.client_secret = str(client_secret)

        session.save()

        try:
            existing_credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            existing_credential = None

        payload = {
            "session_id": str(session.id),
            "state": state,
            "expires_at": expires_at.isoformat(),
            "has_existing_credentials": existing_credential is not None,
            "client_id": session.client_id or "",
        }
        return JsonResponse(payload, status=201)

    def _register_dynamic_client(self, request: HttpRequest, metadata: dict, callback_url: str, config: MCPServerConfig) -> tuple[str, str]:
        endpoint = metadata.get("registration_endpoint")
        if not endpoint:
            raise ValueError("OAuth server does not advertise a registration endpoint.")

        redirect_uri = callback_url
        payload = {
            "client_name": f"Gobii MCP - {config.display_name}",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_basic",
        }
        if metadata.get("scope"):
            payload["scope"] = metadata["scope"]
        elif metadata.get("scopes_supported"):
            payload["scope"] = " ".join(metadata["scopes_supported"])

        response = httpx.post(endpoint, json=payload, timeout=10.0)
        response.raise_for_status()
        client_info = response.json()
        client_id = client_info.get("client_id")
        client_secret = client_info.get("client_secret") or ""
        if not client_id:
            raise ValueError("Client registration response missing client_id")
        return str(client_id), str(client_secret)


class MCPOAuthSessionVerifierView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, session_id: uuid.UUID, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        code_verifier = body.get("code_verifier")
        if not code_verifier:
            return HttpResponseBadRequest("code_verifier is required")

        session = _require_active_session(request, session_id)
        session.code_verifier = str(code_verifier)

        if "code_challenge" in body:
            session.code_challenge = str(body.get("code_challenge") or "")
        if "code_challenge_method" in body:
            session.code_challenge_method = str(body.get("code_challenge_method") or "")
        session.save(update_fields=["code_verifier_encrypted", "code_challenge", "code_challenge_method", "updated_at"])
        return JsonResponse({"status": "ok"})


class MCPOAuthMetadataProxyView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        config_id = body.get("server_config_id")
        resource = body.get("resource") or body.get("path") or body.get("url")
        if not config_id or not resource:
            return HttpResponseBadRequest("server_config_id and resource are required")

        config = _resolve_mcp_server_config(request, str(config_id))
        base_url = config.url
        if not base_url:
            return HttpResponseBadRequest("This MCP server does not define a base URL.")

        target_url = urljoin(base_url, str(resource))
        parsed_base = urlparse(base_url)
        parsed_target = urlparse(target_url)

        if parsed_target.scheme not in {"http", "https"}:
            return HttpResponseBadRequest("Unsupported URL scheme for metadata request.")

        if parsed_target.netloc and parsed_target.netloc != parsed_base.netloc:
            return HttpResponseForbidden("Metadata requests must target the configured MCP host.")

        headers = body.get("headers") or {}
        if headers and not isinstance(headers, dict):
            return HttpResponseBadRequest("headers must be a JSON object")

        try:
            response = httpx.get(target_url, headers=headers or None, timeout=10.0)
        except httpx.HTTPError as exc:
            return JsonResponse(
                {"error": "Failed to contact MCP server", "detail": str(exc)},
                status=502,
            )

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            try:
                payload = response.json()
            except ValueError:
                payload = {"content": response.text}
                return JsonResponse(payload, status=response.status_code)
            else:
                safe = isinstance(payload, dict)
                return JsonResponse(payload, status=response.status_code, safe=safe)

        # Non-JSON responses are wrapped for the client to interpret.
        return JsonResponse(
            {
                "content": response.text,
                "content_type": content_type,
                "status_code": response.status_code,
            },
            status=response.status_code,
        )


class MCPOAuthCallbackView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        session_id_raw = body.get("session_id")
        authorization_code = body.get("authorization_code")
        if not session_id_raw or not authorization_code:
            return HttpResponseBadRequest("session_id and authorization_code are required")

        try:
            session_id = uuid.UUID(str(session_id_raw))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Invalid session_id")

        session = _require_active_session(request, session_id)

        state = body.get("state")
        if state and state != session.state:
            return HttpResponseBadRequest("State mismatch for OAuth session.")

        token_endpoint = body.get("token_endpoint") or session.token_endpoint
        if not token_endpoint:
            return HttpResponseBadRequest("token_endpoint is required to complete the OAuth flow.")

        client_id = body.get("client_id") or session.client_id or ""
        client_secret = body.get("client_secret") or session.client_secret or ""
        redirect_uri = body.get("redirect_uri") or session.redirect_uri or request.build_absolute_uri(reverse("console-mcp-oauth-callback-view"))
        headers = body.get("headers") or {}
        if headers and not isinstance(headers, dict):
            return HttpResponseBadRequest("headers must be a JSON object")

        data = {
            "grant_type": "authorization_code",
            "code": authorization_code,
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        if session.code_verifier:
            data["code_verifier"] = session.code_verifier
        if client_id:
            data["client_id"] = client_id
        if client_secret:
            data["client_secret"] = client_secret

        try:
            response = httpx.post(token_endpoint, data=data, headers=headers or None, timeout=15.0)
        except httpx.HTTPError as exc:
            return JsonResponse({"error": "Token exchange failed", "detail": str(exc)}, status=502)

        if response.status_code >= 400:
            return JsonResponse(
                {
                    "error": "Token endpoint returned an error",
                    "status_code": response.status_code,
                    "body": response.text,
                },
                status=response.status_code,
            )

        try:
            token_payload = response.json()
        except ValueError:
            return JsonResponse(
                {"error": "Token endpoint returned non-JSON payload", "body": response.text},
                status=502,
            )

        access_token = token_payload.get("access_token")
        if not access_token:
            return JsonResponse({"error": "Token response missing access_token"}, status=502)

        config = session.server_config
        try:
            credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            credential = MCPServerOAuthCredential(server_config=config)

        credential.organization = config.organization
        credential.user = config.user
        credential.client_id = client_id
        if client_secret:
            credential.client_secret = client_secret
        credential.access_token = access_token
        credential.refresh_token = token_payload.get("refresh_token")
        credential.id_token = token_payload.get("id_token")
        credential.token_type = token_payload.get("token_type", credential.token_type)
        credential.scope = token_payload.get("scope") or session.scope

        expires_in = token_payload.get("expires_in")
        if expires_in is not None:
            try:
                expires_seconds = int(expires_in)
                credential.expires_at = timezone.now() + timedelta(seconds=max(expires_seconds, 0))
            except (TypeError, ValueError):
                credential.expires_at = None

        metadata = dict(credential.metadata or {})
        metadata_update = body.get("metadata") or {}
        if isinstance(metadata_update, dict):
            metadata.update(metadata_update)
        metadata["token_endpoint"] = token_endpoint
        metadata["last_token_response"] = {
            key: value
            for key, value in token_payload.items()
            if key not in {"access_token", "refresh_token", "id_token"}
        }
        credential.metadata = metadata
        credential.save()

        session.delete()

        try:
            get_mcp_manager().refresh_server(str(config.id))
        except Exception:
            logger.exception("Failed to refresh MCP manager after OAuth callback for %s", config.id)

        payload = {
            "connected": True,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "scope": credential.scope,
            "token_type": credential.token_type,
        }
        return JsonResponse(payload, status=200)


class MCPOAuthStatusView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, server_config_id: uuid.UUID, *args: Any, **kwargs: Any):
        config = _resolve_mcp_server_config(request, str(server_config_id))
        try:
            credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            return JsonResponse({"connected": False})

        payload = {
            "connected": True,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "scope": credential.scope,
            "token_type": credential.token_type,
            "has_refresh_token": bool(credential.refresh_token),
            "updated_at": credential.updated_at.isoformat() if credential.updated_at else None,
        }
        return JsonResponse(payload)


class MCPOAuthRevokeView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, server_config_id: uuid.UUID, *args: Any, **kwargs: Any):
        config = _resolve_mcp_server_config(request, str(server_config_id))
        try:
            credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            return JsonResponse({"revoked": False, "detail": "No stored credentials found."}, status=404)

        credential.delete()
        try:
            get_mcp_manager().refresh_server(str(config.id))
        except Exception:
            logger.exception("Failed to refresh MCP manager after OAuth revoke for %s", config.id)
        return JsonResponse({"revoked": True})


class AgentEmailOAuthStartView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        account_id = body.get("account_id")
        if not account_id:
            return HttpResponseBadRequest("account_id is required")

        account = _resolve_agent_email_account(request, str(account_id))

        metadata = body.get("metadata") or {}
        if metadata and not isinstance(metadata, dict):
            return HttpResponseBadRequest("metadata must be a JSON object")
        provider = str(body.get("provider") or "").strip()
        if provider and "provider" not in metadata:
            metadata["provider"] = provider
        if body.get("use_gobii_app"):
            metadata.setdefault("managed_app", True)

        scope_raw = body.get("scope") or ""
        if isinstance(scope_raw, list):
            scope = " ".join(str(part) for part in scope_raw if part)
        else:
            scope = str(scope_raw)

        expires_at = timezone.now() + timedelta(minutes=10)
        state = str(body.get("state") or secrets.token_urlsafe(32))

        callback_url = body.get("redirect_uri") or request.build_absolute_uri(
            reverse("console-email-oauth-callback-view")
        )

        manual_client_id = str(body.get("client_id") or "")
        manual_client_secret = str(body.get("client_secret") or "")
        use_gobii_app = bool(
            body.get("use_gobii_app")
            or (provider.lower() in MANAGED_EMAIL_PROVIDER_KEYS and not manual_client_id)
        )
        client_id = manual_client_id
        client_secret = manual_client_secret

        if use_gobii_app:
            managed_client_id, managed_client_secret = _resolve_managed_email_oauth_client(provider)
            if not managed_client_id:
                return JsonResponse(
                    {"error": "Gobii OAuth app is not configured for this provider."},
                    status=400,
                )
            client_id = managed_client_id
            client_secret = managed_client_secret
        elif provider.lower() == "generic" and not client_id:
            return JsonResponse(
                {"error": "OAuth client ID is required for generic providers."},
                status=400,
            )

        if not client_id and metadata.get("registration_endpoint"):
            try:
                client_id, client_secret = self._register_dynamic_client(
                    request,
                    metadata,
                    callback_url,
                    account,
                )
            except ValueError as exc:
                return JsonResponse({"error": str(exc)}, status=400)
            except httpx.HTTPError as exc:
                return JsonResponse(
                    {"error": "Client registration failed", "detail": str(exc)},
                    status=502,
                )

        session = AgentEmailOAuthSession(
            account=account,
            initiated_by=request.user,
            user=account.endpoint.owner_agent.user,
            organization=getattr(account.endpoint.owner_agent, "organization", None),
            state=state,
            redirect_uri=callback_url,
            scope=scope,
            code_challenge=str(body.get("code_challenge") or ""),
            code_challenge_method=str(body.get("code_challenge_method") or ""),
            token_endpoint=str(body.get("token_endpoint") or ""),
            client_id=client_id,
            metadata=metadata,
            expires_at=expires_at,
        )

        code_verifier = body.get("code_verifier")
        if code_verifier:
            session.code_verifier = str(code_verifier)

        if client_secret:
            session.client_secret = str(client_secret)

        session.save()

        try:
            existing_credential = account.oauth_credential
        except AgentEmailOAuthCredential.DoesNotExist:
            existing_credential = None

        payload = {
            "session_id": str(session.id),
            "state": state,
            "expires_at": expires_at.isoformat(),
            "has_existing_credentials": existing_credential is not None,
            "client_id": session.client_id or "",
        }
        return JsonResponse(payload, status=201)

    def _register_dynamic_client(self, request: HttpRequest, metadata: dict, callback_url: str, account: AgentEmailAccount) -> tuple[str, str]:
        endpoint = metadata.get("registration_endpoint")
        if not endpoint:
            raise ValueError("OAuth server does not advertise a registration endpoint.")

        agent = getattr(account.endpoint, "owner_agent", None)
        redirect_uri = callback_url
        payload = {
            "client_name": f"Gobii Email - {getattr(agent, 'name', 'Agent')}",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_basic",
        }
        if metadata.get("scope"):
            payload["scope"] = metadata["scope"]
        elif metadata.get("scopes_supported"):
            payload["scope"] = " ".join(metadata["scopes_supported"])

        response = httpx.post(endpoint, json=payload, timeout=10.0)
        response.raise_for_status()
        client_info = response.json()
        client_id = client_info.get("client_id")
        client_secret = client_info.get("client_secret") or ""
        if not client_id:
            raise ValueError("Client registration response missing client_id")
        return str(client_id), str(client_secret)


class AgentEmailOAuthSessionVerifierView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, session_id: uuid.UUID, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        code_verifier = body.get("code_verifier")
        if not code_verifier:
            return HttpResponseBadRequest("code_verifier is required")

        session = _require_active_email_oauth_session(request, session_id)
        session.code_verifier = str(code_verifier)

        if "code_challenge" in body:
            session.code_challenge = str(body.get("code_challenge") or "")
        if "code_challenge_method" in body:
            session.code_challenge_method = str(body.get("code_challenge_method") or "")
        session.save(update_fields=["code_verifier_encrypted", "code_challenge", "code_challenge_method", "updated_at"])
        return JsonResponse({"status": "ok"})


class AgentEmailOAuthCallbackView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        session_id_raw = body.get("session_id")
        authorization_code = body.get("authorization_code")
        if not session_id_raw or not authorization_code:
            return HttpResponseBadRequest("session_id and authorization_code are required")

        try:
            session_id = uuid.UUID(str(session_id_raw))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Invalid session_id")

        session = _require_active_email_oauth_session(request, session_id)

        state = body.get("state")
        if state and state != session.state:
            return HttpResponseBadRequest("State mismatch for OAuth session.")

        token_endpoint = body.get("token_endpoint") or session.token_endpoint
        if not token_endpoint:
            return HttpResponseBadRequest("token_endpoint is required to complete the OAuth flow.")

        client_id = body.get("client_id") or session.client_id or ""
        client_secret = body.get("client_secret") or session.client_secret or ""
        redirect_uri = body.get("redirect_uri") or session.redirect_uri or request.build_absolute_uri(
            reverse("console-email-oauth-callback-view")
        )
        headers = body.get("headers") or {}
        if headers and not isinstance(headers, dict):
            return HttpResponseBadRequest("headers must be a JSON object")

        data = {
            "grant_type": "authorization_code",
            "code": authorization_code,
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        if session.code_verifier:
            data["code_verifier"] = session.code_verifier
        if client_id:
            data["client_id"] = client_id
        if client_secret:
            data["client_secret"] = client_secret

        try:
            response = httpx.post(token_endpoint, data=data, headers=headers or None, timeout=15.0)
        except httpx.HTTPError as exc:
            return JsonResponse({"error": "Token exchange failed", "detail": str(exc)}, status=502)

        if response.status_code >= 400:
            return JsonResponse(
                {
                    "error": "Token endpoint returned an error",
                    "status_code": response.status_code,
                    "body": response.text,
                },
                status=response.status_code,
            )

        try:
            token_payload = response.json()
        except ValueError:
            return JsonResponse(
                {"error": "Token endpoint returned non-JSON payload", "body": response.text},
                status=502,
            )

        access_token = token_payload.get("access_token")
        if not access_token:
            return JsonResponse({"error": "Token response missing access_token"}, status=502)

        account = session.account
        try:
            credential = account.oauth_credential
        except AgentEmailOAuthCredential.DoesNotExist:
            credential = AgentEmailOAuthCredential(account=account, user=account.endpoint.owner_agent.user)

        credential.organization = getattr(account.endpoint.owner_agent, "organization", None)
        credential.user = account.endpoint.owner_agent.user
        credential.client_id = client_id
        if client_secret:
            credential.client_secret = client_secret
        credential.access_token = access_token
        credential.refresh_token = token_payload.get("refresh_token")
        credential.id_token = token_payload.get("id_token")
        credential.token_type = token_payload.get("token_type", credential.token_type)
        credential.scope = token_payload.get("scope") or session.scope

        provider = ""
        if isinstance(session.metadata, dict):
            provider = str(session.metadata.get("provider") or "")
        if provider:
            credential.provider = provider

        expires_in = token_payload.get("expires_in")
        if expires_in is not None:
            try:
                expires_seconds = int(expires_in)
                credential.expires_at = timezone.now() + timedelta(seconds=max(expires_seconds, 0))
            except (TypeError, ValueError):
                credential.expires_at = None

        metadata = dict(credential.metadata or {})
        metadata_update = body.get("metadata") or {}
        if isinstance(metadata_update, dict):
            metadata.update(metadata_update)
        metadata["token_endpoint"] = token_endpoint
        metadata["last_token_response"] = {
            key: value
            for key, value in token_payload.items()
            if key not in {"access_token", "refresh_token", "id_token"}
        }
        credential.metadata = metadata
        credential.save()

        session.delete()

        payload = {
            "connected": True,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "scope": credential.scope,
            "token_type": credential.token_type,
            "provider": credential.provider,
        }
        return JsonResponse(payload, status=200)


class AgentEmailOAuthStatusView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, account_id: uuid.UUID, *args: Any, **kwargs: Any):
        account = _resolve_agent_email_account(request, str(account_id))
        try:
            credential = account.oauth_credential
        except AgentEmailOAuthCredential.DoesNotExist:
            return JsonResponse({"connected": False})

        payload = {
            "connected": True,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "scope": credential.scope,
            "token_type": credential.token_type,
            "has_refresh_token": bool(credential.refresh_token),
            "updated_at": credential.updated_at.isoformat() if credential.updated_at else None,
            "provider": credential.provider,
        }
        return JsonResponse(payload)


class AgentEmailOAuthRevokeView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, account_id: uuid.UUID, *args: Any, **kwargs: Any):
        account = _resolve_agent_email_account(request, str(account_id))
        try:
            credential = account.oauth_credential
        except AgentEmailOAuthCredential.DoesNotExist:
            return JsonResponse({"revoked": False, "detail": "No stored credentials found."}, status=404)

        credential.delete()
        return JsonResponse({"revoked": True})


class MCPServerAssignmentsAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def _serialize_assignments(self, server: MCPServerConfig) -> dict[str, object]:
        assignable = list(mcp_server_service.assignable_agents(server))
        assigned_ids = mcp_server_service.server_assignment_agent_ids(server)
        agents_payload = []
        assigned_count = 0
        for agent in assignable:
            agent_id = str(agent.id)
            is_assigned = agent_id in assigned_ids
            if is_assigned:
                assigned_count += 1
            agents_payload.append(
                {
                    "id": agent_id,
                    "name": agent.name,
                    "description": agent.short_description or "",
                    "is_active": agent.is_active,
                    "assigned": is_assigned,
                    "organization_id": str(agent.organization_id) if agent.organization_id else None,
                    "last_interaction_at": agent.last_interaction_at.isoformat() if agent.last_interaction_at else None,
                }
            )
        return {
            "server": {
                "id": str(server.id),
                "display_name": server.display_name,
                "scope": server.scope,
                "scope_label": server.get_scope_display(),
            },
            "agents": agents_payload,
            "total_agents": len(assignable),
            "assigned_count": assigned_count,
        }

    def get(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        if server.scope == MCPServerConfig.Scope.PLATFORM:
            return HttpResponseBadRequest("Platform-managed servers do not support manual assignments.")
        payload = self._serialize_assignments(server)
        return JsonResponse(payload)

    def post(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        if server.scope == MCPServerConfig.Scope.PLATFORM:
            return HttpResponseBadRequest("Platform-managed servers do not support manual assignments.")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        agent_ids_raw = payload.get("agent_ids", [])
        if not isinstance(agent_ids_raw, list):
            return HttpResponseBadRequest("agent_ids must be a list.")
        agent_ids = [str(agent_id) for agent_id in agent_ids_raw]

        try:
            mcp_server_service.set_server_assignments(server, agent_ids)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        response_payload = self._serialize_assignments(server)
        response_payload["message"] = "Assignments updated."
        return JsonResponse(response_payload)


def _parse_ttl(payload: dict | None) -> int:
    if not payload:
        return WEB_SESSION_TTL_SECONDS
    ttl_raw = payload.get("ttl_seconds")
    if ttl_raw is None:
        return WEB_SESSION_TTL_SECONDS
    try:
        ttl = int(ttl_raw)
    except (TypeError, ValueError):
        raise ValueError("ttl_seconds must be an integer")
    return max(10, ttl)


def _parse_session_key(payload: dict | None) -> str:
    key = (payload or {}).get("session_key")
    if not key:
        raise ValueError("session_key is required")
    return str(key)


def _session_response(result) -> JsonResponse:
    session = result.session
    payload = {
        "session_key": str(session.session_key),
        "ttl_seconds": result.ttl_seconds,
        "expires_at": result.expires_at.isoformat(),
        "last_seen_at": session.last_seen_at.isoformat(),
        "last_seen_source": session.last_seen_source,
    }
    if session.ended_at:
        payload["ended_at"] = session.ended_at.isoformat()
    return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionStartAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            ttl = _parse_ttl(body)
            result = start_web_session(agent, request.user, ttl_seconds=ttl)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.WEB_CHAT_SESSION_STARTED,
            source=AnalyticsSource.WEB,
            properties=_web_chat_properties(
                agent,
                {
                    "session_key": str(result.session.session_key),
                    "session_ttl_seconds": result.ttl_seconds,
                },
            ),
        )

        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionHeartbeatAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            ttl = _parse_ttl(body)
            session_key = _parse_session_key(body)
            result = heartbeat_web_session(
                session_key,
                agent,
                request.user,
                ttl_seconds=ttl,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionEndAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            session_key = _parse_session_key(body)
            result = end_web_session(session_key, agent, request.user)
        except ValueError as exc:
            if str(exc) == "Unknown web session.":
                return JsonResponse({"session_key": session_key, "ended": True})
            return HttpResponseBadRequest(str(exc))

        session = result.session
        props = {
            "session_key": str(session.session_key),
            "session_ttl_seconds": result.ttl_seconds,
        }
        if session.ended_at:
            props["session_ended_at"] = session.ended_at.isoformat()

        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.WEB_CHAT_SESSION_ENDED,
            source=AnalyticsSource.WEB,
            properties=_web_chat_properties(agent, props),
        )

        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteListAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        suites = []
        for suite in sorted(SuiteRegistry.list_all().values(), key=lambda s: s.slug):
            suites.append(
                {
                    "slug": suite.slug,
                    "description": suite.description,
                    "scenario_slugs": list(suite.scenario_slugs),
                }
            )
        return JsonResponse({"suites": suites})


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        MAX_REQUESTED_RUNS = 10

        try:
            body = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        suite_slugs = body.get("suite_slugs") or ["all"]
        if not isinstance(suite_slugs, list) or not suite_slugs:
            return HttpResponseBadRequest("suite_slugs must be a non-empty list")

        agent_strategy = body.get("agent_strategy") or EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO
        if agent_strategy not in dict(EvalSuiteRun.AgentStrategy.choices):
            return HttpResponseBadRequest("Invalid agent_strategy")

        shared_agent: PersistentAgent | None = None
        run_type_raw = body.get("run_type") or EvalSuiteRun.RunType.ONE_OFF
        if isinstance(body.get("official"), bool):
            run_type_raw = EvalSuiteRun.RunType.OFFICIAL if body.get("official") else EvalSuiteRun.RunType.ONE_OFF
        if isinstance(run_type_raw, str):
            run_type_raw = run_type_raw.lower()
        if run_type_raw not in dict(EvalSuiteRun.RunType.choices):
            return HttpResponseBadRequest("Invalid run_type")
        run_type: str = run_type_raw

        n_runs_raw = body.get("n_runs") if "n_runs" in body else body.get("runs")
        if n_runs_raw is None:
            requested_runs = 3
        else:
            try:
                requested_runs = int(n_runs_raw)
            except (TypeError, ValueError):
                return HttpResponseBadRequest(f"n_runs must be an integer between 1 and {MAX_REQUESTED_RUNS}")
        if requested_runs < 1 or requested_runs > MAX_REQUESTED_RUNS:
            return HttpResponseBadRequest(f"n_runs must be between 1 and {MAX_REQUESTED_RUNS}")

        # Optional LLM routing profile for the eval
        from api.models import LLMRoutingProfile
        from api.services.llm_routing_profile_snapshot import create_eval_profile_snapshot
        source_routing_profile = None
        llm_routing_profile_id = body.get("llm_routing_profile_id")
        if llm_routing_profile_id:
            try:
                source_routing_profile = LLMRoutingProfile.objects.get(
                    id=llm_routing_profile_id,
                    is_eval_snapshot=False,  # Don't allow selecting an existing snapshot
                )
            except LLMRoutingProfile.DoesNotExist:
                return HttpResponseBadRequest("LLM routing profile not found")

        agent_id = body.get("agent_id")
        if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT:
            if not agent_id:
                return HttpResponseBadRequest("agent_id is required when reusing an agent")
            try:
                shared_agent = PersistentAgent.objects.get(id=agent_id)
            except PersistentAgent.DoesNotExist:
                return HttpResponseBadRequest("Agent not found")

        def create_ephemeral_agent(label_suffix: str) -> PersistentAgent:
            unique_id = f"{label_suffix}-{uuid.uuid4().hex[:8]}" if label_suffix else uuid.uuid4().hex[:12]
            browser_agent = BrowserUseAgent.objects.create(name=f"Eval Browser {unique_id}", user=request.user)
            return PersistentAgent.objects.create(
                name=f"Eval Agent {unique_id}",
                user=request.user,
                browser_use_agent=browser_agent,
                execution_environment="eval",
                charter="You are a test agent.",
            )

        created_suite_runs: list[EvalSuiteRun] = []
        created_runs: list[EvalRun] = []

        for suite_slug in suite_slugs:
            suite_obj = SuiteRegistry.get(suite_slug)
            if not suite_obj:
                return HttpResponseBadRequest(f"Suite '{suite_slug}' not found")

            scenario_slugs = list(dict.fromkeys(suite_obj.scenario_slugs))

            # Create a temporary suite run ID to use for snapshot naming
            temp_suite_run_id = uuid.uuid4()

            # Create a snapshot of the profile if one was specified
            profile_snapshot = None
            if source_routing_profile:
                profile_snapshot = create_eval_profile_snapshot(
                    source_routing_profile,
                    str(temp_suite_run_id),
                )

            suite_run = EvalSuiteRun.objects.create(
                id=temp_suite_run_id,
                suite_slug=suite_obj.slug,
                initiated_by=request.user,
                status=EvalSuiteRun.Status.RUNNING,
                run_type=run_type,
                requested_runs=requested_runs,
                agent_strategy=agent_strategy,
                shared_agent=shared_agent if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT else None,
                started_at=timezone.now(),
                llm_routing_profile=profile_snapshot,
            )

            created_for_suite = 0
            for scenario_slug in scenario_slugs:
                scenario = ScenarioRegistry.get(scenario_slug)
                if not scenario:
                    continue

                for iteration in range(requested_runs):
                    run_agent = shared_agent
                    if agent_strategy == EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO or run_agent is None:
                        suffix = f"{scenario.slug[:8]}-{iteration + 1}" if requested_runs > 1 else scenario.slug[:8]
                        run_agent = create_ephemeral_agent(label_suffix=suffix)

                    run = EvalRun.objects.create(
                        suite_run=suite_run,
                        scenario_slug=scenario.slug,
                        scenario_version=getattr(scenario, "version", "") or "",
                        agent=run_agent,
                        initiated_by=request.user,
                        status=EvalRun.Status.PENDING,
                        run_type=run_type,
                    )
                    run_eval_task.delay(str(run.id))
                    created_runs.append(run)
                    created_for_suite += 1

            if created_for_suite == 0:
                suite_run.status = EvalSuiteRun.Status.ERRORED
                suite_run.finished_at = timezone.now()
                suite_run.save(update_fields=["status", "finished_at", "updated_at"])
            created_suite_runs.append(suite_run)

        # Update suite aggregate state and return payload
        response_suites = []
        for suite_run in created_suite_runs:
            _update_suite_state(suite_run.id)
            suite_run.refresh_from_db()
            response_suites.append(_serialize_suite_run(suite_run, include_runs=True, include_tasks=False))

        # Trigger background GC to clean up any stale runs
        try:
            gc_eval_runs_task.delay()
        except Exception:
            logger.debug("Failed to enqueue eval GC task", exc_info=True)

        return JsonResponse(
            {
                "suite_runs": response_suites,
                "agent_strategy": agent_strategy,
                "runs": [str(run.id) for run in created_runs],
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunListAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        status_filter = request.GET.get("status")
        suite_filter = request.GET.get("suite")
        run_type_filter = request.GET.get("run_type")
        limit_raw = request.GET.get("limit") or "25"
        try:
            limit = max(1, min(100, int(limit_raw)))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")
        if run_type_filter:
            run_type_filter = run_type_filter.lower()
            if run_type_filter not in dict(EvalSuiteRun.RunType.choices):
                return HttpResponseBadRequest("Invalid run_type")

        qs = (
            EvalSuiteRun.objects.select_related("initiated_by", "shared_agent")
            .prefetch_related("runs__tasks")
        )
        if status_filter:
            qs = qs.filter(status=status_filter)
        if suite_filter:
            qs = qs.filter(suite_slug=suite_filter)
        if run_type_filter:
            qs = qs.filter(run_type=run_type_filter)

        suite_runs = list(qs.order_by("-created_at")[:limit])
        # Refresh stale aggregates so UI doesn't show stuck "running" rows
        for suite in suite_runs:
            _update_suite_state(suite.id)

        suite_runs = list(
            EvalSuiteRun.objects.filter(id__in=[suite.id for suite in suite_runs])
            .select_related("initiated_by", "shared_agent")
            .prefetch_related("runs__tasks")
            .order_by("-created_at")
        )
        payload = [_serialize_suite_run(suite, include_runs=True, include_tasks=False) for suite in suite_runs]
        return JsonResponse({"suite_runs": payload})


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunDetailAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, suite_run_id: str, *args: Any, **kwargs: Any):
        _update_suite_state(suite_run_id)
        suite = get_object_or_404(
            EvalSuiteRun.objects.prefetch_related("runs__tasks", "runs__agent"),
            pk=suite_run_id,
        )
        return JsonResponse({"suite_run": _serialize_suite_run(suite, include_runs=True, include_tasks=True)})


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunRunTypeAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, suite_run_id: str, *args: Any, **kwargs: Any):
        try:
            body = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        run_type_raw = body.get("run_type")
        if isinstance(body.get("official"), bool):
            run_type_raw = EvalSuiteRun.RunType.OFFICIAL if body.get("official") else EvalSuiteRun.RunType.ONE_OFF
        if isinstance(run_type_raw, str):
            run_type_raw = run_type_raw.lower()
        if run_type_raw not in dict(EvalSuiteRun.RunType.choices):
            return HttpResponseBadRequest("Invalid run_type")

        suite = get_object_or_404(
            EvalSuiteRun.objects.prefetch_related("runs__tasks"),
            pk=suite_run_id,
        )

        if suite.run_type != run_type_raw:
            suite.run_type = run_type_raw
            suite.save(update_fields=["run_type", "updated_at"])
            now = timezone.now()
            EvalRun.objects.filter(suite_run_id=suite.id).update(run_type=run_type_raw, updated_at=now)

        suite = EvalSuiteRun.objects.prefetch_related("runs__tasks").get(pk=suite_run_id)

        broadcast_suite_update(suite, include_runs=True)
        for run in suite.runs.all():
            broadcast_run_update(run, include_tasks=True)

        return JsonResponse({"suite_run": _serialize_suite_run(suite, include_runs=True, include_tasks=True)})


@method_decorator(csrf_exempt, name="dispatch")
class EvalRunDetailAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, run_id: str, *args: Any, **kwargs: Any):
        run = get_object_or_404(
            EvalRun.objects.prefetch_related("tasks"),
            pk=run_id,
        )
        payload = _serialize_eval_run(run, include_tasks=True)

        # Add comparison metadata if fingerprint exists
        if run.scenario_fingerprint:
            comparable_count = EvalRun.objects.filter(
                scenario_fingerprint=run.scenario_fingerprint,
                status=EvalRun.Status.COMPLETED,
            ).exclude(id=run.id).count()
            payload["comparison"] = {
                "comparable_runs_count": comparable_count,
                "has_comparable_runs": comparable_count > 0,
            }

        return JsonResponse({"run": payload})


@method_decorator(csrf_exempt, name="dispatch")
class EvalRunCompareAPIView(SystemAdminAPIView):
    """
    Get runs comparable to a given run.

    Supports three comparison tiers via ?tier= parameter:
    - strict: Same fingerprint + same LLM profile lineage (most rigorous)
    - pragmatic (default): Same fingerprint, any config
    - historical: Same scenario slug, any fingerprint (loosest)

    Supports grouping via ?group_by= parameter:
    - code_version: Group by git commit (isolate code changes)
    - primary_model: Group by LLM model (compare models)
    - llm_profile: Group by routing profile (compare configs)

    Additional filters to hold variables constant:
    - ?code_version=: Filter to specific git commit
    - ?primary_model=: Filter to specific model
    """
    http_method_names = ["get"]

    def get(self, request: HttpRequest, run_id: str, *args: Any, **kwargs: Any):
        from django.db.models import Avg, Count, Sum
        from django.db.models.functions import Coalesce

        run = get_object_or_404(EvalRun, pk=run_id)

        tier = request.GET.get("tier", "pragmatic").lower()
        if tier not in ("strict", "pragmatic", "historical"):
            return HttpResponseBadRequest("tier must be one of: strict, pragmatic, historical")

        group_by = request.GET.get("group_by")
        if group_by and group_by not in ("code_version", "primary_model", "llm_profile"):
            return HttpResponseBadRequest("group_by must be one of: code_version, primary_model, llm_profile")

        run_type_filter = request.GET.get("run_type")
        if run_type_filter:
            run_type_filter = run_type_filter.lower()
            if run_type_filter not in dict(EvalRun.RunType.choices):
                return HttpResponseBadRequest("Invalid run_type")

        # Additional filters to hold variables constant
        code_version_filter = request.GET.get("code_version")
        primary_model_filter = request.GET.get("primary_model")

        limit_raw = request.GET.get("limit", "50")
        try:
            limit = max(1, min(100, int(limit_raw)))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")

        # Build query based on tier
        qs = EvalRun.objects.filter(status=EvalRun.Status.COMPLETED)

        if tier == "strict":
            # Same fingerprint + same LLM profile lineage
            if not run.scenario_fingerprint:
                return JsonResponse({
                    "runs": [],
                    "groups": [],
                    "tier": tier,
                    "target_run_id": str(run.id),
                    "warning": "Target run has no fingerprint - cannot do strict comparison",
                })
            qs = qs.filter(scenario_fingerprint=run.scenario_fingerprint)
            # Filter by LLM profile lineage if the run has one
            if run.llm_routing_profile_id:
                profile = run.llm_routing_profile
                source_id = profile.cloned_from_id if profile.cloned_from_id else profile.id
                qs = qs.filter(
                    models.Q(llm_routing_profile_id=source_id) |
                    models.Q(llm_routing_profile__cloned_from_id=source_id)
                )
        elif tier == "pragmatic":
            # Same fingerprint, any config
            if not run.scenario_fingerprint:
                return JsonResponse({
                    "runs": [],
                    "groups": [],
                    "tier": tier,
                    "target_run_id": str(run.id),
                    "warning": "Target run has no fingerprint - falling back to slug matching",
                })
            qs = qs.filter(scenario_fingerprint=run.scenario_fingerprint)
        else:  # historical
            # Same scenario slug, any fingerprint
            qs = qs.filter(scenario_slug=run.scenario_slug)

        # Apply additional filters
        if run_type_filter:
            qs = qs.filter(run_type=run_type_filter)
        if code_version_filter:
            qs = qs.filter(code_version=code_version_filter)
        if primary_model_filter:
            qs = qs.filter(primary_model=primary_model_filter)

        # Check for fingerprint mismatches in historical tier
        fingerprint_warning = None
        if tier == "historical" and run.scenario_fingerprint:
            mismatched_count = qs.exclude(scenario_fingerprint=run.scenario_fingerprint).count()
            if mismatched_count:
                fingerprint_warning = f"{mismatched_count} run(s) have different fingerprints - eval code may have changed"

        # Handle grouping
        if group_by:
            group_field = {
                "code_version": "code_version",
                "primary_model": "primary_model",
                "llm_profile": "llm_routing_profile_name",
            }[group_by]

            groups = (
                qs.values(group_field)
                .annotate(
                    run_count=Count("id"),
                    avg_cost=Avg("total_cost"),
                    avg_tokens=Avg("tokens_used"),
                    total_tasks=Sum("step_count"),
                    # Pass rate requires counting tasks - simplified here
                )
                .order_by("-run_count")[:limit]
            )

            # Enrich with pass rate by fetching task stats
            groups_list = []
            for g in groups:
                group_value = g[group_field]
                group_runs = qs.filter(**{group_field: group_value}).prefetch_related("tasks")

                # Calculate pass rate across all runs in group
                total_passed = 0
                total_tasks = 0
                for gr in group_runs:
                    for task in gr.tasks.all():
                        total_tasks += 1
                        if task.status == "passed":
                            total_passed += 1

                groups_list.append({
                    "group_by": group_by,
                    "value": group_value or "(none)",
                    "run_count": g["run_count"],
                    "avg_cost": float(g["avg_cost"]) if g["avg_cost"] else 0,
                    "avg_tokens": float(g["avg_tokens"]) if g["avg_tokens"] else 0,
                    "pass_rate": (total_passed / total_tasks * 100) if total_tasks > 0 else 0,
                    "total_tasks": total_tasks,
                    "passed_tasks": total_passed,
                    "is_current": group_value == getattr(run, group_field),
                })

            return JsonResponse({
                "groups": groups_list,
                "group_by": group_by,
                "tier": tier,
                "target_run_id": str(run.id),
                "target_fingerprint": run.scenario_fingerprint or None,
                "fingerprint_warning": fingerprint_warning,
                "filters": {
                    "code_version": code_version_filter,
                    "primary_model": primary_model_filter,
                    "run_type": run_type_filter,
                },
            })

        # Non-grouped: return individual runs (excluding current run)
        runs = list(qs.exclude(id=run.id).order_by("-finished_at")[:limit].prefetch_related("tasks"))

        return JsonResponse({
            "runs": [_serialize_eval_run(r, include_tasks=False) for r in runs],
            "tier": tier,
            "target_run_id": str(run.id),
            "target_fingerprint": run.scenario_fingerprint or None,
            "fingerprint_warning": fingerprint_warning,
        })


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunCompareAPIView(SystemAdminAPIView):
    """
    Compare suite runs at the aggregate level (across all scenarios).

    Supports three comparison tiers via ?tier= parameter:
    - strict: Same suite + all scenario fingerprints must match
    - pragmatic (default): Same suite + same scenario slugs
    - historical: Same suite slug only (loosest)

    Supports grouping via ?group_by= parameter:
    - code_version: Group by git commit (isolate code changes)
    - primary_model: Group by primary LLM model (compare models)
    - llm_profile: Group by routing profile (compare configs)
    """
    http_method_names = ["get"]

    def get(self, request: HttpRequest, suite_run_id: str, *args: Any, **kwargs: Any):
        from django.db.models import Avg, Count, Sum

        suite_run = get_object_or_404(
            EvalSuiteRun.objects.prefetch_related("runs__tasks"),
            pk=suite_run_id,
        )

        tier = request.GET.get("tier", "pragmatic").lower()
        if tier not in ("strict", "pragmatic", "historical"):
            return HttpResponseBadRequest("tier must be one of: strict, pragmatic, historical")

        group_by = request.GET.get("group_by")
        if group_by and group_by not in ("code_version", "primary_model", "llm_profile"):
            return HttpResponseBadRequest("group_by must be one of: code_version, primary_model, llm_profile")

        run_type_filter = request.GET.get("run_type")
        if run_type_filter:
            run_type_filter = run_type_filter.lower()
            if run_type_filter not in dict(EvalSuiteRun.RunType.choices):
                return HttpResponseBadRequest("Invalid run_type")

        limit_raw = request.GET.get("limit", "50")
        try:
            limit = max(1, min(100, int(limit_raw)))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")

        # Get fingerprints and scenario slugs from target suite
        target_runs = list(suite_run.runs.all())
        target_fingerprints = {r.scenario_fingerprint for r in target_runs if r.scenario_fingerprint}
        target_scenario_slugs = {r.scenario_slug for r in target_runs}

        # Get primary model from first run (for "is_current" detection)
        target_primary_model = target_runs[0].primary_model if target_runs else None
        target_code_version = target_runs[0].code_version if target_runs else None
        target_llm_profile = target_runs[0].llm_routing_profile_name if target_runs else None

        # Build query for comparable suite runs
        qs = EvalSuiteRun.objects.filter(
            suite_slug=suite_run.suite_slug,
            status=EvalSuiteRun.Status.COMPLETED,
        ).prefetch_related("runs__tasks")

        if tier == "strict":
            # Same suite + all scenario fingerprints must match
            # Filter to suites that have runs with ALL the same fingerprints
            if not target_fingerprints:
                return JsonResponse({
                    "suite_runs": [],
                    "groups": [],
                    "tier": tier,
                    "target_suite_run_id": str(suite_run.id),
                    "warning": "Target suite has no fingerprints - cannot do strict comparison",
                })
            # We'll filter after fetching since this requires checking all runs
        elif tier == "pragmatic":
            # Same suite + same scenario slugs (fingerprints may differ)
            pass  # We'll filter after fetching
        # historical: just same suite_slug, already filtered

        if run_type_filter:
            qs = qs.filter(run_type=run_type_filter)

        # Fetch all candidate suites
        candidate_suites = list(qs.order_by("-finished_at")[:limit * 3])  # Fetch extra for filtering

        # Filter based on tier
        comparable_suites = []
        fingerprint_warning = None
        mismatched_count = 0

        for candidate in candidate_suites:
            candidate_runs = list(candidate.runs.all())
            candidate_fingerprints = {r.scenario_fingerprint for r in candidate_runs if r.scenario_fingerprint}
            candidate_slugs = {r.scenario_slug for r in candidate_runs}

            if tier == "strict":
                # All fingerprints must match exactly
                if candidate_fingerprints == target_fingerprints:
                    comparable_suites.append(candidate)
                elif candidate_slugs == target_scenario_slugs:
                    mismatched_count += 1
            elif tier == "pragmatic":
                # Same scenario slugs required
                if candidate_slugs == target_scenario_slugs:
                    comparable_suites.append(candidate)
                    if candidate_fingerprints != target_fingerprints:
                        mismatched_count += 1
            else:  # historical
                # Any suite with same suite_slug
                comparable_suites.append(candidate)
                if candidate_fingerprints != target_fingerprints:
                    mismatched_count += 1

            if len(comparable_suites) >= limit:
                break

        if mismatched_count > 0 and tier in ("pragmatic", "historical"):
            fingerprint_warning = f"{mismatched_count} suite(s) have different scenario fingerprints - eval code may have changed"

        # Helper to calculate suite stats
        def calc_suite_stats(suite: EvalSuiteRun) -> dict:
            runs = list(suite.runs.all())
            total_passed = 0
            total_tasks = 0
            total_cost = 0.0
            total_tokens = 0

            for run in runs:
                total_cost += float(run.total_cost or 0)
                total_tokens += run.tokens_used or 0
                for task in run.tasks.all():
                    total_tasks += 1
                    if task.status == "passed":
                        total_passed += 1

            return {
                "passed": total_passed,
                "total": total_tasks,
                "pass_rate": (total_passed / total_tasks * 100) if total_tasks > 0 else 0,
                "total_cost": total_cost,
                "total_tokens": total_tokens,
                "primary_model": runs[0].primary_model if runs else None,
                "code_version": runs[0].code_version if runs else None,
                "llm_profile": runs[0].llm_routing_profile_name if runs else None,
            }

        # Handle grouping
        if group_by:
            # Group comparable suites by the specified field
            groups_map: dict[str, list] = {}
            for suite in comparable_suites:
                stats = calc_suite_stats(suite)
                if group_by == "code_version":
                    key = stats["code_version"] or "(none)"
                elif group_by == "primary_model":
                    key = stats["primary_model"] or "(none)"
                else:  # llm_profile
                    key = stats["llm_profile"] or "(none)"

                if key not in groups_map:
                    groups_map[key] = []
                groups_map[key].append({
                    "suite": suite,
                    "stats": stats,
                })

            # Aggregate stats per group
            groups_list = []
            for key, items in groups_map.items():
                total_passed = sum(i["stats"]["passed"] for i in items)
                total_tasks = sum(i["stats"]["total"] for i in items)
                total_cost = sum(i["stats"]["total_cost"] for i in items)
                total_tokens = sum(i["stats"]["total_tokens"] for i in items)
                suite_count = len(items)

                # Determine if this is the current group
                if group_by == "code_version":
                    is_current = key == (target_code_version or "(none)")
                elif group_by == "primary_model":
                    is_current = key == (target_primary_model or "(none)")
                else:
                    is_current = key == (target_llm_profile or "(none)")

                groups_list.append({
                    "group_by": group_by,
                    "value": key,
                    "suite_count": suite_count,
                    "run_count": suite_count,  # For compatibility with frontend
                    "avg_cost": total_cost / suite_count if suite_count > 0 else 0,
                    "avg_tokens": total_tokens / suite_count if suite_count > 0 else 0,
                    "pass_rate": (total_passed / total_tasks * 100) if total_tasks > 0 else 0,
                    "total_tasks": total_tasks,
                    "passed_tasks": total_passed,
                    "is_current": is_current,
                })

            # Sort by pass rate descending
            groups_list.sort(key=lambda x: x["pass_rate"], reverse=True)

            return JsonResponse({
                "groups": groups_list,
                "group_by": group_by,
                "tier": tier,
                "target_suite_run_id": str(suite_run.id),
                "fingerprint_warning": fingerprint_warning,
                "filters": {
                    "run_type": run_type_filter,
                },
            })

        # Non-grouped: return individual suite runs
        suite_runs_data = []
        for suite in comparable_suites:
            if suite.id == suite_run.id:
                continue  # Exclude current suite
            stats = calc_suite_stats(suite)
            suite_runs_data.append({
                "id": str(suite.id),
                "suite_slug": suite.suite_slug,
                "status": suite.status,
                "run_type": suite.run_type,
                "started_at": suite.started_at.isoformat() if suite.started_at else None,
                "finished_at": suite.finished_at.isoformat() if suite.finished_at else None,
                "code_version": stats["code_version"],
                "primary_model": stats["primary_model"],
                "llm_profile": stats["llm_profile"],
                "pass_rate": stats["pass_rate"],
                "total_cost": stats["total_cost"],
                "total_tokens": stats["total_tokens"],
                "passed_tasks": stats["passed"],
                "total_tasks": stats["total"],
            })

        return JsonResponse({
            "suite_runs": suite_runs_data,
            "tier": tier,
            "target_suite_run_id": str(suite_run.id),
            "fingerprint_warning": fingerprint_warning,
        })
