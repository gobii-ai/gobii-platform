import json
import logging
import mimetypes
import os
import secrets
import shutil
import tempfile
import time
import uuid
import base64
import zipfile
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from smtplib import SMTPException
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import zstandard as zstd
from allauth.core.exceptions import ImmediateHttpResponse
from anymail.exceptions import AnymailAPIError, AnymailError
from celery.exceptions import CeleryError
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.exceptions import PermissionDenied, RequestDataTooBig, ValidationError
from django.core.mail import BadHeaderError
from django.db import IntegrityError, models, transaction
from django.db.models import Max, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import get_object_or_404
from django.template.defaultfilters import filesizeformat
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.urls import NoReverseMatch, reverse
from django.utils.text import get_valid_filename
from kombu.exceptions import OperationalError as KombuOperationalError

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.human_input_requests import dismiss_human_input_request, submit_human_input_response, submit_human_input_responses_batch
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.comms.message_reads import (
    READ_SOURCE_CHAT_OPEN,
    build_latest_agent_message_read_state,
    mark_latest_visible_outbound_message_read,
    serialize_agent_message_read_state,
    serialize_latest_agent_message_read_state,
)
from api.agent.core.processing_flags import bump_human_inbound_generation, clear_processing_stop_requested, clear_processing_work_state, set_processing_stop_requested
from api.agent.core.agent_judge import approve_judge_suggestion, dismiss_judge_suggestion, run_manual_agent_judge
from api.domain_validation import DomainPatternValidator
from api.agent.files.attachment_helpers import load_signed_filespace_download_payload
from api.agent.files.filespace_service import dedupe_name, get_or_create_default_filespace
from api.agent.tasks.reported_message_judge import run_reported_agent_judge_task
from api.agent.tools.mcp_manager import get_mcp_manager
from marketing_events.custom_events import ConfiguredCustomEvent, emit_configured_custom_capi_event
from pages.public_template_urls import public_template_detail_path
from api.models import (
    BrowserLLMPolicy,
    BrowserUseAgentTask,
    BrowserLLMTier,
    BrowserModelEndpoint,
    BrowserTierEndpoint,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    EmbeddingsLLMTier,
    EmbeddingsModelEndpoint,
    EmbeddingsTierEndpoint,
    FileHandlerLLMTier,
    FileHandlerModelEndpoint,
    FileHandlerTierEndpoint,
    ImageGenerationLLMTier,
    ImageGenerationModelEndpoint,
    ImageGenerationTierEndpoint,
    VideoGenerationLLMTier,
    VideoGenerationModelEndpoint,
    VideoGenerationTierEndpoint,
    IntelligenceTier,
    LLMProvider,
    LLMRoutingProfile,
    MCPServerConfig,
    MCPServerOAuthCredential,
    MCPServerOAuthSession,
    AgentEmailAccount,
    AgentEmailOAuthCredential,
    AgentEmailOAuthSession,
    AgentCollaboratorInvite,
    AgentTransferInvite,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentHumanInputRequest,
    PersistentAgentJudgeSuggestion,
    PersistentAgentMessage,
    PersistentAgentMessageFeedback,
    PersistentAgentSecret,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemMessage,
    PersistentAgentSystemStep,
    PersistentAgentStep,
    PersistentAgentTemplate,
    PersistentLLMTier,
    PersistentModelEndpoint,
    PersistentTierEndpoint,
    PersistentTokenRange,
    PublicProfile,
    ProfileBrowserTierEndpoint,
    ProfileBrowserTier,
    ProfileEmbeddingsTier,
    ProfileEmbeddingsTierEndpoint,
    ProfilePersistentTier,
    ProfilePersistentTierEndpoint,
    ProfileTokenRange,
    PersistentAgentPromptArchive,
    SmsContactPurpose,
    AgentFileSpaceAccess,
    AgentFsNode,
    Organization,
    OrganizationMembership,
    UserPhoneNumber,
    UserPreference,
    UserEmail,
    AddonEntitlement,
    TaskCredit,
    build_web_agent_address,
    build_web_user_address,
    get_agent_contact_counts,
)
from api.public_profiles import generate_handle_suggestion
from django.core.files.storage import default_storage
from agents.services import PretrainedWorkerTemplateService
from config.socialaccount_adapter import OAUTH_CHARTER_COOKIE, restore_oauth_session_state
from console.agent_audit.export import InvalidAuditExportRange, build_audit_export_range, write_agent_audit_export_json
from console.agent_audit.serializers import serialize_system_message
from console.llm_tier_usage import build_browser_endpoint_tier_usage, build_persistent_endpoint_tier_usage
from api.encryption import SecretsEncryption
from api.agent.tasks import process_agent_events_task, queue_agent_process_events_batch_task
from api.services.system_settings import get_max_file_size
from api.services.owner_execution_pause import get_owner_account_pause_state
from api.services.agent_owner_custom_instructions import (
    CUSTOM_INSTRUCTIONS_FIELD,
    CustomInstructionsValidationError,
    get_custom_instructions_for_user_id,
    normalize_custom_instructions,
    save_custom_instructions_for_user_id,
)
from api.services.product_announcements import build_product_announcements_payload, mark_product_announcements_read
from api.services.signup_preview import resume_signup_preview_agent_if_eligible, user_has_existing_personal_agent_for_signup_preview
from api.services.email_verification import (
    EMAIL_CHANGE_REDIRECT_URL,
    cancel_email_change,
    get_email_verification_target,
    send_email_verification,
    serialize_email_verification as _serialize_email_verification,
    start_email_change,
    validate_email_change,
)
from api.services.agent_planning import skip_agent_planning
from api.services.referral_service import ReferralService
from api.services.web_sessions import WEB_SESSION_TTL_SECONDS, end_web_session, heartbeat_web_session, start_web_session, touch_web_session
from api.services.sms_contact_purpose import sms_contact_purpose_required, track_sms_contact_approval

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.onboarding import TRIAL_ONBOARDING_TARGET_AGENT_UI, set_trial_onboarding_intent, set_trial_onboarding_requires_plan_selection
from util.personal_signup_preview import resolve_personal_signup_preview, resolve_personal_signup_preview_onboarding_state
from util.trial_enforcement import PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE, TrialRequiredValidationError, can_user_send_personal_agent_chat_message
from util.subscription_helper import get_user_max_contacts_per_agent
from util.urls import IMMERSIVE_APP_BASE_PATH, append_context_query, build_staff_developer_chat_path_for_agent

from console.agent_chat.access import (
    agent_queryset_for,
    resolve_agent_for_request,
    resolve_manageable_agent_for_request,
    shared_agent_queryset_for,
    user_can_manage_agent,
    user_can_manage_agent_settings,
    user_has_natural_agent_chat_access,
    resolve_staff_agent,
    user_is_collaborator,
)
from console.agent_chat.pending_actions import (
    count_pending_action_requests_for_agents,
    expire_pending_action_requests,
    get_legacy_pending_human_input_requests,
    list_pending_action_requests,
    serialize_contact_request,
)
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE,
    TimelineDirection,
    build_processing_activity_map,
    build_processing_snapshot,
    compute_processing_status,
    fetch_timeline_window,
    serialize_agent_schedule,
    serialize_message_event,
    serialize_processing_snapshot,
    serialize_user_action_event,
)
from console.agent_chat.developer_timeline import fetch_developer_timeline_window
from console.agent_chat.user_actions import (
    record_contact_requests_resolved,
    record_human_input_answered,
    record_human_input_dismissed,
    record_requested_secrets_removed,
    record_requested_secrets_saved,
)
from console.agent_chat.suggestions import DEFAULT_PROMPT_COUNT, build_agent_timeline_suggestions
from console.agent_chat.template_recommendations import build_new_agent_template_recommendations
from console.api_helpers import ApiLoginRequiredMixin, _coerce_bool, _parse_json_body
from console.context_helpers import build_console_context, resolve_console_context, resolve_staff_console_context
from console.context_overrides import get_context_override, get_staff_context_override
from console.agent_context import resolve_context_override_for_agent
from console.billing_initial_data import build_billing_initial_data
from console.forms import MCPServerConfigForm, PhoneVerifyForm, UserProfileForm
from console.phone_utils import PhoneVerificationSendError, get_pending_phone, get_phone_cooldown_remaining, get_primary_phone, send_phone_verification, serialize_phone, serialize_phone_state
from constants.phone_countries import serialize_supported_phone_regions
from console.agent_quick_settings import build_agent_quick_settings_payload
from console.system_status import build_system_status_payload
from console.support_requests import SupportRequestConfigurationError, clean_support_message, send_agent_message_report_email, send_app_support_request
from console.agent_cards import enrich_agents_for_card_surface, serialize_agent_card_payload
from console.agent_settings import build_agent_settings_payload, handle_agent_settings_mutation
from console.views import build_llm_intelligence_props
from console.agent_addons import (
    _build_billing_status_payload,
    build_account_pause_payload,
    build_agent_addons_payload,
    update_contact_pack_quantities,
    update_task_pack_quantities,
)
from console.daily_credit import parse_daily_credit_limit
from console.agent_creation import (
    AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY,
    AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY,
    AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE,
    AGENT_TEMPLATE_SOURCE_PUBLIC_TEMPLATE,
    AGENT_TEMPLATE_SOURCE_SESSION_KEY,
    enable_agent_sms_contact,
    stage_agent_template_session,
)
from console.views import _track_org_event_for_console, _mcp_server_event_properties
from api.views import PersistentAgentViewSet, cancel_browser_use_task
from api.services.sandbox_compute import SANDBOX_COMPUTE_WAFFLE_FLAG, SandboxComputeService, SandboxComputeUnavailable
from waffle import flag_is_active
from console.llm_serializers import build_llm_overview, serialize_intelligence_tier
import litellm

from api.agent.core.llm_config import invalidate_llm_bootstrap_cache
from api.agent.core.llm_utils import run_completion
from api.pipedream_app_utils import normalize_app_slugs
from api.llm.utils import normalize_model_name, normalize_pricing_model
from api.openrouter import DEFAULT_API_BASE, get_attribution_headers
from api.services import mcp_servers as mcp_server_service
from api.services.template_clone import TemplateCloneError, TemplateCloneService
from api.services.spawn_requests import SpawnRequestResolutionError, SpawnRequestService
from api.services.persistent_agent_secrets import ensure_global_secret_capacity_for_agent, move_agent_secret_to_global, validate_agent_secret_globalization
from api.services.daily_credit_limits import get_agent_credit_multiplier
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from api.services.agent_settings_resume import queue_owner_task_pack_resume, queue_settings_change_resume
from api.services.system_settings import clear_setting_value, get_setting_definition, list_system_settings, serialize_setting, set_setting_value
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices
from tasks.services import TaskCreditService
from util.integrations import stripe_status
from util.subscription_helper import get_active_subscription, get_stripe_customer, get_organization_plan, reconcile_user_plan_from_stripe, get_user_plan
from util.constants.task_constants import TASKS_UNLIMITED
from console.role_constants import BILLING_MANAGE_ROLES


logger = logging.getLogger(__name__)


def _resolve_request_context_owner(request: HttpRequest):
    try:
        override = get_context_override(request)
        context_info = resolve_console_context(
            request.user,
            request.session,
            override=override,
        )
    except PermissionDenied:
        return None

    if context_info.current_context.type == "organization":
        return Organization.objects.filter(id=context_info.current_context.id).first()
    return request.user


def _customer_account_pause_block_message(owner) -> str:
    state = get_owner_account_pause_state(owner)
    resume_at = state.get("resume_at")
    if resume_at is not None:
        local_resume_at = timezone.localtime(resume_at)
        return (
            "Your account is paused until "
            f"{local_resume_at.strftime('%b %d, %Y at %I:%M %p %Z')}. "
            "New messages and agent creation are disabled until billing resumes."
        )
    return "Your account is paused. New messages and agent creation are disabled until billing resumes."
User = get_user_model()


GOOGLE_PROVIDER_KEYS = {"gmail", "google"}
MICROSOFT_PROVIDER_KEYS = {"outlook", "o365", "office365", "microsoft"}
MANAGED_EMAIL_PROVIDER_KEYS = GOOGLE_PROVIDER_KEYS | MICROSOFT_PROVIDER_KEYS


def _can_manage_contact_packs(request: HttpRequest, agent: PersistentAgent, plan_payload: dict | None) -> bool:
    if not stripe_status().enabled:
        return False
    plan_id = str((plan_payload or {}).get("id") or "").lower()
    if not plan_id or plan_id == PlanNamesChoices.FREE.value:
        return False

    if not agent.organization_id:
        return bool(agent.user_id == request.user.id or request.user.is_staff or request.user.is_superuser)

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


def _can_open_agent_billing(request: HttpRequest, agent: PersistentAgent) -> bool:
    if not agent.organization_id:
        return bool(agent.user_id == request.user.id or request.user.is_staff or request.user.is_superuser)

    membership = OrganizationMembership.objects.filter(
        user=request.user,
        org=agent.organization,
        status=OrganizationMembership.OrgStatus.ACTIVE,
    ).first()
    return bool(membership and membership.role in BILLING_MANAGE_ROLES)


def _can_user_resolve_spawn_requests(
    user,
    agent: PersistentAgent,
    *,
    allow_delinquent_personal_chat: bool = False,
) -> bool:
    return user_can_manage_agent_settings(
        user,
        agent,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    )


def _format_validation_error(error: ValidationError) -> str:
    if hasattr(error, "message_dict") and error.message_dict:
        messages: list[str] = []
        for field_errors in error.message_dict.values():
            messages.extend(str(message) for message in field_errors)
        if messages:
            return " ".join(messages)
    if hasattr(error, "messages") and error.messages:
        return " ".join(str(message) for message in error.messages)
    return str(error)


def _pending_action_payload(agent: PersistentAgent, viewer_user) -> dict[str, Any]:
    expire_pending_action_requests(agent)
    pending_action_requests = list_pending_action_requests(agent, viewer_user)
    return {
        "pending_human_input_requests": get_legacy_pending_human_input_requests(pending_action_requests),
        "pending_action_requests": pending_action_requests,
    }


def _user_action_event_payload(action_event, viewer_user) -> dict[str, Any]:
    return (
        {"event": serialize_user_action_event(action_event, viewer_user=viewer_user)}
        if action_event
        else {}
    )


def _emit_pending_action_requests_update_on_commit(agent: PersistentAgent) -> None:
    from console.agent_chat.signals import emit_pending_action_requests_update

    emit_pending_action_requests_update(agent)


class ConsolePersistentAgentViewSet(PersistentAgentViewSet):
    """
    Reuse the API serializer/update flow for the console detail route.

    The base API viewset resolves ownership from API auth, which does not map to the
    console's personal/org context switching rules. The console route therefore has
    to resolve the target agent through the console access helpers first.
    """

    def _resolve_console_agent(self, agent_id: str) -> PersistentAgent:
        if self.action in {"partial_update", "update", "destroy"}:
            return resolve_manageable_agent_for_request(
                self.request,
                agent_id,
                allow_delinquent_personal_chat=True,
            )
        return resolve_agent_for_request(
            self.request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )

    def get_object(self):
        agent_id = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        if agent_id is None:
            raise Http404("Agent not found.")

        agent = self._resolve_console_agent(str(agent_id))
        self._resolved_console_agent = agent
        self.check_object_permissions(self.request, agent)
        return agent

    def _request_organization(self):
        organization = super()._request_organization()
        if organization is not None:
            return organization

        # Console requests are session-authenticated, so org context comes from the
        # resolved agent rather than API-key auth.
        agent = getattr(self, "_resolved_console_agent", None)
        if agent is not None and agent.organization_id:
            return agent.organization
        return None


class ConsoleSessionAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse(
            {
                "user_id": str(request.user.id),
                "email": request.user.email,
                "timezone": UserPreference.resolve_user_timezone(request.user),
                "is_system_admin": bool(request.user.is_staff or request.user.is_superuser),
            }
        )


class AppSupportRequestAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
            if not isinstance(payload, dict):
                return JsonResponse({"ok": False, "message": "Invalid request payload."}, status=400)
            message = clean_support_message(payload.get("message"))
        except ValueError as exc:
            return JsonResponse({"ok": False, "message": str(exc)}, status=400)

        try:
            send_app_support_request(user=request.user, payload=payload)
        except SupportRequestConfigurationError as exc:
            return JsonResponse({"ok": False, "message": str(exc)}, status=500)
        except (AnymailAPIError, BadHeaderError, OSError, SMTPException):
            logger.exception("Failed to send in-app support request for user %s.", request.user.id)
            return JsonResponse(
                {"ok": False, "message": "Unable to send support request. Please try again later."},
                status=500,
            )

        workspace_context = payload.get("workspaceContext")
        if not isinstance(workspace_context, dict):
            workspace_context = {}
        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.SUPPORT_REQUEST_SUBMITTED,
            source=AnalyticsSource.WEB,
            properties={
                "message_length": len(message),
                "page_url": payload.get("pageUrl") if isinstance(payload.get("pageUrl"), str) else "",
                "agent_id": payload.get("agentId") if isinstance(payload.get("agentId"), str) else "",
                "agent_name": payload.get("agentName") if isinstance(payload.get("agentName"), str) else "",
                "workspace_context_type": workspace_context.get("type") if isinstance(workspace_context.get("type"), str) else "",
                "workspace_context_id": workspace_context.get("id") if isinstance(workspace_context.get("id"), str) else "",
            },
        )
        return JsonResponse({"ok": True})


class UserPreferencesAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "patch"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse({"preferences": UserPreference.resolve_known_preferences(request.user)})

    def patch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        if not isinstance(payload, dict):
            return HttpResponseBadRequest("JSON body must be an object.")

        unknown_top_level_keys = sorted(key for key in payload.keys() if key != "preferences")
        if unknown_top_level_keys:
            return HttpResponseBadRequest(
                f"Unknown top-level fields: {', '.join(unknown_top_level_keys)}"
            )

        if "preferences" not in payload:
            return HttpResponseBadRequest("Missing 'preferences' field.")

        raw_preferences = payload["preferences"]
        try:
            resolved_preferences = UserPreference.update_known_preferences(request.user, raw_preferences)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        return JsonResponse({"preferences": resolved_preferences})


class ProductAnnouncementListAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse(build_product_announcements_payload(request.user))


class ProductAnnouncementReadAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        allowed_keys = {"all", "announcementIds"}
        unknown_keys = sorted(key for key in payload if key not in allowed_keys)
        if unknown_keys:
            return HttpResponseBadRequest(
                f"Unknown top-level fields: {', '.join(unknown_keys)}"
            )

        mark_all_requested = "all" in payload
        ids_requested = "announcementIds" in payload
        if mark_all_requested == ids_requested:
            return HttpResponseBadRequest("Provide either 'all' or 'announcementIds'.")

        if mark_all_requested:
            if payload.get("all") is not True:
                return HttpResponseBadRequest("'all' must be true.")
            return JsonResponse(mark_product_announcements_read(request.user, mark_all=True))

        raw_ids = payload.get("announcementIds")
        if not isinstance(raw_ids, list) or not raw_ids:
            return HttpResponseBadRequest("'announcementIds' must be a non-empty array.")

        announcement_ids: list[uuid.UUID] = []
        for raw_id in raw_ids:
            if not isinstance(raw_id, str):
                return HttpResponseBadRequest("'announcementIds' must contain UUID strings.")
            try:
                announcement_ids.append(uuid.UUID(raw_id))
            except ValueError:
                return HttpResponseBadRequest("'announcementIds' must contain UUID strings.")

        return JsonResponse(
            mark_product_announcements_read(
                request.user,
                announcement_ids=announcement_ids,
            )
        )


def _serialize_user_profile_options(form: UserProfileForm) -> list[dict[str, str]]:
    return [
        {
            "value": str(value),
            "label": str(label),
        }
        for value, label in form.fields["timezone"].choices
    ]


def _serialize_user_profile_payload(request: HttpRequest) -> dict[str, Any]:
    user = request.user
    form = UserProfileForm(instance=user)
    base_url = request.build_absolute_uri("/").rstrip("/")
    phone = get_primary_phone(user)
    pending_phone = get_pending_phone(user)
    verified_phone = phone if phone and phone.is_verified else None
    return {
        "profile": {
            "firstName": user.first_name or "",
            "lastName": user.last_name or "",
            "timezone": form.fields["timezone"].initial or "",
        },
        "timezoneOptions": _serialize_user_profile_options(form),
        "customInstructions": get_custom_instructions_for_user_id(user.id),
        "customInstructionsMaxChars": settings.AGENT_OWNER_CUSTOM_INSTRUCTIONS_MAX_CHARS,
        "referralLink": ReferralService.get_referral_link(
            user,
            base_url=base_url,
            track=False,
        ),
        "emailVerification": _serialize_email_verification(user),
        "phone": serialize_phone(verified_phone),
        "pendingPhone": serialize_phone(pending_phone),
        "supportedPhoneRegions": serialize_supported_phone_regions(),
    }


def _serialize_profile_form_errors(form: UserProfileForm) -> dict[str, list[str]]:
    field_names = {
        "first_name": "firstName",
        "last_name": "lastName",
        "timezone": "timezone",
        "__all__": "nonFieldErrors",
    }
    return {
        field_names.get(field, field): [str(error) for error in errors]
        for field, errors in form.errors.items()
    }


class UserProfileAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "patch"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse(_serialize_user_profile_payload(request))

    @transaction.atomic
    def patch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        custom_instructions_supplied = CUSTOM_INSTRUCTIONS_FIELD in payload
        profile_supplied = "profile" in payload
        unknown_fields = sorted(key for key in payload if key not in {"profile", CUSTOM_INSTRUCTIONS_FIELD})
        if unknown_fields:
            return JsonResponse(
                {"errors": {"nonFieldErrors": [f"Unsupported top-level fields: {', '.join(unknown_fields)}"]}},
                status=400,
            )
        if not profile_supplied and not custom_instructions_supplied:
            return JsonResponse(
                {"errors": {"nonFieldErrors": ["Provide profile or customInstructions."]}},
                status=400,
            )

        normalized_instructions = None
        if custom_instructions_supplied:
            try:
                normalized_instructions = normalize_custom_instructions(payload.get(CUSTOM_INSTRUCTIONS_FIELD))
            except CustomInstructionsValidationError as exc:
                return JsonResponse({"errors": {CUSTOM_INSTRUCTIONS_FIELD: [str(exc)]}}, status=400)

        if profile_supplied:
            raw_profile = payload["profile"]
            if not isinstance(raw_profile, dict):
                return JsonResponse({"errors": {"profile": ["Profile must be an object."]}}, status=400)

            current_form = UserProfileForm(instance=request.user)
            form_data = {
                "first_name": raw_profile.get("firstName", raw_profile.get("first_name", request.user.first_name or "")),
                "last_name": raw_profile.get("lastName", raw_profile.get("last_name", request.user.last_name or "")),
                "timezone": raw_profile.get("timezone", current_form.fields["timezone"].initial or ""),
            }
            form = UserProfileForm(form_data, instance=request.user)
            if not form.is_valid():
                return JsonResponse({"errors": _serialize_profile_form_errors(form)}, status=400)
            form.save()

        if custom_instructions_supplied and normalized_instructions is not None:
            save_custom_instructions_for_user_id(
                request.user.id,
                instructions=normalized_instructions,
                updated_by=request.user,
            )
        return JsonResponse(_serialize_user_profile_payload(request))


class AgentSpawnIntentAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        from api.agent.core.llm_config import resolve_preferred_tier_for_owner
        PREFERRED_LLM_TIER_SESSION_KEY = "agent_preferred_llm_tier"

        restored_cookie = False
        if "agent_charter" not in request.session:
            restored_cookie = restore_oauth_session_state(request, overwrite_existing=False)

        resolved_context = build_console_context(request)
        saved_charter = request.session.get("agent_charter")
        preview_config = resolve_personal_signup_preview(
            request.user,
            request=request,
            current_context_type=resolved_context.current_context.type,
        )
        onboarding_state = resolve_personal_signup_preview_onboarding_state(
            request,
            preview_config=preview_config,
        )
        preferred_llm_tier_raw = (request.session.get(PREFERRED_LLM_TIER_SESSION_KEY) or "").strip()
        preferred_llm_tier = None
        if preferred_llm_tier_raw:
            # Do not plan-clamp here; plan clamping happens when the agent is persisted and at runtime.
            preferred_llm_tier = resolve_preferred_tier_for_owner(None, preferred_llm_tier_raw).value

        payload = {
            "charter": saved_charter,
            "charter_override": request.session.get("agent_charter_override"),
            "preferred_llm_tier": preferred_llm_tier,
            "selected_pipedream_app_slugs": request.session.get(
                AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY
            )
            or [],
            "onboarding_target": onboarding_state.target if onboarding_state.pending else None,
            "requires_plan_selection": bool(
                onboarding_state.pending and onboarding_state.requires_plan_selection
            ),
            "template_recommendations": build_new_agent_template_recommendations(
                request.user,
                resolved_context,
            ),
        }
        response = JsonResponse(payload)
        if restored_cookie:
            response.delete_cookie(OAUTH_CHARTER_COOKIE)
        return response


def _personal_signup_preview_create_available(request: HttpRequest, context_info) -> bool:
    if context_info.current_context.type != "personal":
        return False
    preview_config = resolve_personal_signup_preview(
        request.user,
        request=request,
        current_context_type=context_info.current_context.type,
    )
    return bool(
        preview_config.processing_limit_enabled
        and not user_has_existing_personal_agent_for_signup_preview(request.user)
    )


def _persist_quick_create_draft(
    request: HttpRequest,
    *,
    initial_message: str,
    preferred_llm_tier_key: str | None,
    charter_override: str | None,
    selected_pipedream_app_slugs: list[str],
) -> None:
    template_code = request.session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY)
    template_source = (request.session.get(AGENT_TEMPLATE_SOURCE_SESSION_KEY) or "").strip()
    template_organization_id = str(request.session.get(AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY) or "").strip()
    preserve_template_attribution = (
        request.session.get("agent_charter_source") == "template" and bool(template_code)
    )

    request.session["agent_charter"] = initial_message
    request.session["agent_charter_source"] = (
        "template" if preserve_template_attribution else "user"
    )

    if preferred_llm_tier_key:
        request.session["agent_preferred_llm_tier"] = preferred_llm_tier_key
    else:
        request.session.pop("agent_preferred_llm_tier", None)

    if charter_override:
        request.session["agent_charter_override"] = charter_override
    else:
        request.session.pop("agent_charter_override", None)

    if selected_pipedream_app_slugs:
        request.session[AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY] = selected_pipedream_app_slugs
    else:
        request.session.pop(AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY, None)

    if preserve_template_attribution:
        request.session[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY] = template_code
        if template_source:
            request.session[AGENT_TEMPLATE_SOURCE_SESSION_KEY] = template_source
        else:
            request.session.pop(AGENT_TEMPLATE_SOURCE_SESSION_KEY, None)
        if template_source == AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE and template_organization_id:
            request.session[AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY] = template_organization_id
        else:
            request.session.pop(AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY, None)
    else:
        # Treat immersive quick-create as a fresh custom draft, not a continuation
        # of a previously selected template.
        request.session.pop(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, None)
        request.session.pop(AGENT_TEMPLATE_SOURCE_SESSION_KEY, None)
        request.session.pop(AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY, None)
    request.session.modified = True


def _template_charter_error(template: PersistentAgentTemplate) -> JsonResponse | None:
    if not (template.charter or "").strip():
        return JsonResponse({"error": "This template is missing a charter."}, status=400)
    return None


def _resolve_quick_create_template(
    request: HttpRequest,
    *,
    template_source: str,
    template_id: str,
    template_code: str,
) -> tuple[PersistentAgentTemplate | None, str, JsonResponse | None]:
    normalized_code = str(template_code or "").strip()
    normalized_id = str(template_id or "").strip()
    normalized_source = {
        "public": AGENT_TEMPLATE_SOURCE_PUBLIC_TEMPLATE,
        "organization": AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE,
    }.get(str(template_source or "").strip().lower(), str(template_source or "").strip().lower())
    if not normalized_source and (normalized_code or normalized_id):
        normalized_source = AGENT_TEMPLATE_SOURCE_PUBLIC_TEMPLATE
    if not normalized_source:
        return None, "", None

    if normalized_source == AGENT_TEMPLATE_SOURCE_PUBLIC_TEMPLATE:
        public_filter = Q()
        if normalized_code:
            public_filter |= Q(code__iexact=normalized_code) | Q(slug__iexact=normalized_code)
        if normalized_id:
            try:
                public_filter |= Q(id=uuid.UUID(normalized_id))
            except (TypeError, ValueError, AttributeError):
                return None, normalized_source, JsonResponse({"error": "template_id must be a valid UUID."}, status=400)
        if not public_filter:
            return None, normalized_source, None
        template = (
            PersistentAgentTemplate.objects
            .select_related("preferred_llm_tier", "organization")
            .filter(organization__isnull=True, is_active=True)
            .filter(public_filter)
            .filter(Q(code__gt="") | Q(slug__gt=""))
            .order_by("priority", "display_name", "id")
            .first()
        )
    elif normalized_source == AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE:
        if not normalized_id:
            return None, normalized_source, JsonResponse({"error": "template_id is required for organization templates."}, status=400)
        try:
            template_uuid = uuid.UUID(normalized_id)
        except (TypeError, ValueError, AttributeError):
            return None, normalized_source, JsonResponse({"error": "template_id must be a valid UUID."}, status=400)
        try:
            resolved_context = build_console_context(request)
        except PermissionDenied:
            return None, normalized_source, JsonResponse({"error": "Invalid context override."}, status=403)
        if resolved_context.current_context.type != "organization" or resolved_context.current_membership is None:
            return None, normalized_source, JsonResponse({"error": "Switch to the organization context to use this template."}, status=400)
        if not resolved_context.can_create_org_agents:
            return None, normalized_source, JsonResponse({"error": "You do not have permission to create agents for this organization."}, status=403)
        template = (
            PersistentAgentTemplate.objects
            .select_related("preferred_llm_tier", "organization")
            .filter(
                id=template_uuid,
                organization_id=resolved_context.current_context.id,
                public_profile__isnull=True,
                is_active=True,
            )
            .first()
        )
    else:
        return None, normalized_source, JsonResponse({"error": "Invalid template source."}, status=400)

    if template is None:
        return None, normalized_source, JsonResponse({"error": "This template is no longer available."}, status=404)
    return template, normalized_source, _template_charter_error(template)


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
            settings.GOOGLE_CLIENT_ID,
            settings.GOOGLE_CLIENT_SECRET,
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
    responses_api: bool = False,
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
    model = normalize_model_name(
        provider,
        raw_model,
        api_base=api_base,
        responses_api=responses_api,
    )
    pricing_model = normalize_pricing_model(endpoint, provider)

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
    if pricing_model:
        params["pricing_model"] = pricing_model
    params["supports_temperature"] = supports_temperature
    if hasattr(endpoint, "supports_tool_choice"):
        params["supports_tool_choice"] = bool(getattr(endpoint, "supports_tool_choice", True))
    if hasattr(endpoint, "use_parallel_tool_calls"):
        params["use_parallel_tool_calls"] = bool(getattr(endpoint, "use_parallel_tool_calls", True))
    if hasattr(endpoint, "allow_implied_send"):
        params["allow_implied_send"] = bool(getattr(endpoint, "allow_implied_send", True))
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
    if model.startswith("azure/"):
        params["custom_llm_provider"] = "azure"
        params["api_version"] = "v1"

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


def _run_completion_test(
    endpoint,
    provider: LLMProvider,
    *,
    model_attr: str,
    base_attr: str,
    default_max_tokens: int,
    responses_api: bool = False,
) -> dict[str, Any]:
    model, params = _build_completion_params(
        endpoint,
        provider,
        model_attr=model_attr,
        base_attr=base_attr,
        default_max_tokens=default_max_tokens,
        responses_api=responses_api,
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


def _extract_generated_image_url(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return None

    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    if message is None:
        return None

    images = getattr(message, "images", None)
    if images is None and isinstance(message, dict):
        images = message.get("images")
    if isinstance(images, list):
        for image_entry in images:
            image_url = getattr(image_entry, "image_url", None)
            if image_url is None and isinstance(image_entry, dict):
                image_url = image_entry.get("image_url")

            candidate = None
            if isinstance(image_url, str):
                candidate = image_url.strip()
            elif isinstance(image_url, dict):
                candidate = str(image_url.get("url") or "").strip()
            elif image_url is not None:
                candidate = str(getattr(image_url, "url", "")).strip()

            if candidate:
                return candidate

    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type in {"image_url", "image", "output_image"}:
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    candidate = str(image_url.get("url") or "").strip()
                    if candidate:
                        return candidate
                candidate = str(part.get("url") or "").strip()
                if candidate:
                    return candidate

    return None


def _run_image_generation_test(endpoint: ImageGenerationModelEndpoint) -> dict[str, Any]:
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

    params: dict[str, Any] = {
        "api_key": api_key,
        "timeout": 30,
        "max_tokens": 64,
    }
    if api_base:
        params["api_base"] = api_base
    _apply_provider_overrides(provider, params)

    started = time.monotonic()
    response = run_completion(
        model=model,
        messages=[{"role": "user", "content": "Generate a tiny red square icon."}],
        params=params,
        drop_params=True,
        modalities=["image", "text"],
        image_config={"aspect_ratio": "1:1"},
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    preview = _extract_completion_preview(response)
    image_url = _extract_generated_image_url(response)
    if not image_url:
        raise ValueError("No generated image was returned by the endpoint")

    image_bytes: int | None = None
    if image_url.startswith("data:") and "," in image_url:
        header, payload = image_url.split(",", 1)
        if ";base64" in header.lower():
            try:
                image_bytes = len(base64.b64decode(payload, validate=True))
            except (ValueError, TypeError):
                image_bytes = None

    return {
        "message": "Image generated successfully.",
        "model": model,
        "provider": provider.display_name if provider else "Unlinked",
        "preview": preview,
        "latency_ms": latency_ms,
        "image_bytes": image_bytes,
    }


def _run_video_generation_test(endpoint: VideoGenerationModelEndpoint) -> dict[str, Any]:
    import litellm as _litellm

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

    params: dict[str, Any] = {
        "api_key": api_key,
        "timeout": 120,
    }
    if api_base:
        params["api_base"] = api_base
    _apply_provider_overrides(provider, params)

    gen_kwargs: dict[str, Any] = {
        "prompt": "A gentle wave rolling onto a sandy beach at sunset.",
        "model": model,
        "seconds": "5",
        **params,
    }

    started = time.monotonic()
    video_obj = _litellm.video_generation(**gen_kwargs)

    poll_count = 0
    while video_obj.status not in ("completed", "failed", "expired"):
        if time.monotonic() - started > 120:
            raise ValueError(f"Video generation timed out (status={video_obj.status})")
        time.sleep(5)
        poll_count += 1
        video_obj = _litellm.video_status(video_obj.id, **params)

    if video_obj.status != "completed":
        error_msg = "unknown"
        if video_obj.error and isinstance(video_obj.error, dict):
            error_msg = video_obj.error.get("message", error_msg)
        raise ValueError(f"Video generation failed: {error_msg}")

    latency_ms = int((time.monotonic() - started) * 1000)
    return {
        "message": "Video generated successfully.",
        "model": model,
        "provider": provider.display_name if provider else "Unlinked",
        "latency_ms": latency_ms,
        "video_id": video_obj.id,
        "poll_count": poll_count,
    }


def _is_system_admin_user(user) -> bool:
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def _resolve_mcp_server_config(
    request: HttpRequest,
    config_id: str,
    *,
    allow_platform_staff: bool = False,
) -> MCPServerConfig:
    """Resolve an MCP server configuration the user is allowed to manage."""
    config = get_object_or_404(MCPServerConfig, pk=config_id)
    if config.scope == MCPServerConfig.Scope.PLATFORM:
        if allow_platform_staff and _is_system_admin_user(request.user):
            return config
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


def _resolve_platform_mcp_server_config(request: HttpRequest, config_id: str) -> MCPServerConfig:
    """Resolve a platform MCP server for staff-only management surfaces."""
    if not _is_system_admin_user(request.user):
        raise PermissionDenied("You do not have permission to manage platform MCP servers.")
    return get_object_or_404(
        MCPServerConfig,
        pk=config_id,
        scope=MCPServerConfig.Scope.PLATFORM,
    )


def _require_active_session(request: HttpRequest, session_id: uuid.UUID) -> MCPServerOAuthSession:
    """Fetch a pending OAuth session and enforce ownership + expiry."""
    session = get_object_or_404(MCPServerOAuthSession, pk=session_id)

    if session.initiated_by_id != request.user.id:
        raise PermissionDenied("You do not have access to this OAuth session.")

    if session.has_expired():
        session.delete()
        raise PermissionDenied("OAuth session has expired. Restart the flow.")

    # Re-check access against server configuration in case ownership changed mid-flow.
    _resolve_mcp_server_config(request, str(session.server_config_id), allow_platform_staff=True)
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


def _mcp_server_requires_sandbox_test(server: MCPServerConfig) -> bool:
    return (
        server.scope != MCPServerConfig.Scope.PLATFORM
        and bool(server.command)
        and not bool(server.url)
    )


def _serialize_mcp_test_tool(tool) -> dict[str, object]:
    return {
        "full_name": str(getattr(tool, "full_name", "") or ""),
        "tool_name": str(getattr(tool, "tool_name", "") or ""),
        "server_name": str(getattr(tool, "server_name", "") or ""),
        "description": str(getattr(tool, "description", "") or ""),
        "parameters": getattr(tool, "parameters", None) if isinstance(getattr(tool, "parameters", None), dict) else {},
    }


def _serialize_mcp_test_tool_dict(tool: dict[str, object]) -> dict[str, object]:
    parameters = tool.get("parameters")
    return {
        "full_name": str(tool.get("full_name") or ""),
        "tool_name": str(tool.get("tool_name") or ""),
        "server_name": str(tool.get("server_name") or ""),
        "description": str(tool.get("description") or ""),
        "parameters": parameters if isinstance(parameters, dict) else {},
    }


def _mcp_test_error_response(message: str, *, phase: str, error_type: str, details: dict[str, object] | None = None):
    safe_details: dict[str, object] = {
        "phase": phase,
        "error_type": error_type,
        "message": message,
    }
    if details:
        for key in ("phase", "error_type", "message", "reason", "server_id"):
            value = details.get(key)
            if isinstance(value, (str, int, float, bool)):
                safe_details[key] = value
    return JsonResponse(
        {
            "status": "error",
            "message": message,
            "details": safe_details,
            "sandboxed": False,
            "agent": None,
            "tools": [],
        }
    )


def _resolve_mcp_test_agent(server: MCPServerConfig, agent_id: object) -> PersistentAgent | None:
    agent_id_text = str(agent_id or "").strip()
    if not agent_id_text:
        return None
    return mcp_server_service.assignable_agents(server).filter(id=agent_id_text).first()


def _run_mcp_server_test(server: MCPServerConfig, payload: dict[str, object] | None = None) -> JsonResponse:
    payload = payload or {}
    if not server.is_active:
        return HttpResponseBadRequest("MCP server must be active before it can be tested.")

    if _mcp_server_requires_sandbox_test(server):
        agent = _resolve_mcp_test_agent(server, payload.get("agent_id"))
        if agent is None:
            return HttpResponseBadRequest("agent_id is required and must identify an eligible agent for this MCP server.")
        try:
            result = SandboxComputeService().discover_mcp_tools(
                str(server.id),
                reason="manual_test",
                agent=agent,
            )
        except (SandboxComputeUnavailable, ValueError, RuntimeError) as exc:
            return _mcp_test_error_response(
                "Sandbox MCP discovery could not run.",
                phase="sandbox_discovery",
                error_type=exc.__class__.__name__,
                details={"message": str(exc)},
            )

        if not isinstance(result, dict):
            return _mcp_test_error_response(
                "Sandbox MCP discovery returned an invalid response.",
                phase="sandbox_discovery",
                error_type="invalid_response",
            )
        if result.get("status") != "ok":
            message = str(result.get("message") or "Sandbox MCP discovery failed.")
            return _mcp_test_error_response(
                message,
                phase="sandbox_discovery",
                error_type=str(result.get("error_type") or "sandbox_error"),
                details=result,
            )

        tools = [
            _serialize_mcp_test_tool_dict(tool)
            for tool in result.get("tools", [])
            if isinstance(tool, dict)
        ]
        return JsonResponse(
            {
                "status": "ok",
                "message": f"Discovered {len(tools)} tool{'s' if len(tools) != 1 else ''}.",
                "sandboxed": True,
                "agent": {"id": str(agent.id), "name": agent.name},
                "tools": tools,
            }
        )

    ok, tools, details = get_mcp_manager().test_server_tools(str(server.id))
    if not ok:
        return _mcp_test_error_response(
            str(details.get("message") or "MCP discovery failed."),
            phase=str(details.get("phase") or "discover_tools"),
            error_type=str(details.get("error_type") or "discovery_error"),
            details=details,
        )

    serialized_tools = [_serialize_mcp_test_tool(tool) for tool in tools]
    return JsonResponse(
        {
            "status": "ok",
            "message": f"Discovered {len(serialized_tools)} tool{'s' if len(serialized_tools) != 1 else ''}.",
            "sandboxed": False,
            "agent": None,
            "tools": serialized_tools,
        }
    )


def _form_errors(form: MCPServerConfigForm) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    for field, field_errors in form.errors.items():
        errors[field] = [str(error) for error in field_errors]
    non_field = form.non_field_errors()
    if non_field:
        errors["non_field_errors"] = [str(error) for error in non_field]
    return errors


def _json_ok(**extra):
    payload = {"ok": True}
    payload.update(extra)
    return JsonResponse(payload)


def _json_payload_or_bad_request(request: HttpRequest) -> dict[str, Any] | HttpResponseBadRequest:
    try:
        return _parse_json_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))


def _coerce_provider_browser_backend(value: Any) -> str:
    backend = str(value or LLMProvider.BrowserBackend.OPENAI).strip()
    if backend not in LLMProvider.BrowserBackend.values:
        allowed = ", ".join(LLMProvider.BrowserBackend.values)
        raise ValueError(f"browser_backend must be one of: {allowed}")
    return backend


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


def _next_image_generation_order(use_case: str) -> int:
    last = ImageGenerationLLMTier.objects.filter(use_case=use_case).order_by("-order").first()
    return (last.order if last else 0) + 1


def _next_video_generation_order(use_case: str) -> int:
    last = VideoGenerationLLMTier.objects.filter(use_case=use_case).order_by("-order").first()
    return (last.order if last else 0) + 1


def _create_aux_llm_endpoint_from_payload(
    payload: dict[str, Any],
    *,
    endpoint_model,
    include_supports_vision: bool = False,
    include_supports_image_to_image: bool = False,
    include_supports_image_to_video: bool = False,
) -> tuple[Any | None, HttpResponseBadRequest | None]:
    """Create an embeddings/file-handler style endpoint from request payload."""
    key = (payload.get("key") or "").strip()
    model = (payload.get("model") or payload.get("litellm_model") or "").strip()
    if not key or not model:
        return None, HttpResponseBadRequest("key and model are required")
    if endpoint_model.objects.filter(key=key).exists():
        return None, HttpResponseBadRequest("Endpoint key already exists")

    provider = None
    provider_id = payload.get("provider_id")
    if provider_id:
        provider = get_object_or_404(LLMProvider, pk=provider_id)

    create_kwargs = {
        "key": key,
        "provider": provider,
        "litellm_model": model,
        "litellm_pricing_model": (payload.get("litellm_pricing_model") or "").strip() or None,
        "api_base": (payload.get("api_base") or "").strip(),
        "low_latency": _coerce_bool(payload.get("low_latency", False)),
        "enabled": _coerce_bool(payload.get("enabled", True)),
    }
    if include_supports_vision:
        create_kwargs["supports_vision"] = _coerce_bool(payload.get("supports_vision", False))
    if include_supports_image_to_image:
        create_kwargs["supports_image_to_image"] = _coerce_bool(payload.get("supports_image_to_image", False))
    if include_supports_image_to_video:
        create_kwargs["supports_image_to_video"] = _coerce_bool(payload.get("supports_image_to_video", False))

    endpoint = endpoint_model.objects.create(**create_kwargs)
    return endpoint, None


def _update_aux_llm_endpoint_from_payload(
    endpoint,
    payload: dict[str, Any],
    *,
    include_supports_vision: bool = False,
    include_supports_image_to_image: bool = False,
    include_supports_image_to_video: bool = False,
) -> HttpResponseBadRequest | None:
    """Update an embeddings/file-handler style endpoint from request payload."""
    if "model" in payload or "litellm_model" in payload:
        model = (payload.get("model") or payload.get("litellm_model") or "").strip()
        if model:
            endpoint.litellm_model = model
    if "litellm_pricing_model" in payload:
        endpoint.litellm_pricing_model = (payload.get("litellm_pricing_model") or "").strip() or None

    if "api_base" in payload:
        endpoint.api_base = (payload.get("api_base") or "").strip()
    if include_supports_vision and "supports_vision" in payload:
        endpoint.supports_vision = _coerce_bool(payload.get("supports_vision"))
    if include_supports_image_to_image and "supports_image_to_image" in payload:
        endpoint.supports_image_to_image = _coerce_bool(payload.get("supports_image_to_image"))
    if include_supports_image_to_video and "supports_image_to_video" in payload:
        endpoint.supports_image_to_video = _coerce_bool(payload.get("supports_image_to_video"))
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
    return None


def _delete_endpoint_with_tier_guard(endpoint) -> HttpResponseBadRequest | None:
    if endpoint.in_tiers.exists():
        return HttpResponseBadRequest("Remove endpoint from tiers before deleting")
    endpoint.delete()
    return None


def _create_aux_tier_from_payload(
    payload: dict[str, Any],
    *,
    tier_model,
    next_order_fn,
    extra_create_kwargs: dict[str, Any] | None = None,
):
    description = (payload.get("description") or "").strip()
    order = next_order_fn()
    create_kwargs = {"order": order, "description": description}
    if extra_create_kwargs:
        create_kwargs.update(extra_create_kwargs)
    return tier_model.objects.create(**create_kwargs)


def _update_aux_tier_from_payload(
    tier,
    payload: dict[str, Any],
    *,
    queryset,
) -> HttpResponseBadRequest | None:
    if "description" in payload:
        tier.description = (payload.get("description") or "").strip()
    if "move" in payload:
        direction = (payload.get("move") or "").lower()
        if direction not in {"up", "down"}:
            return HttpResponseBadRequest("direction must be 'up' or 'down'")
        changed = _swap_orders(queryset, tier, direction)
        if not changed:
            return HttpResponseBadRequest("Unable to move tier in that direction")
    tier.save()
    return None


def _create_aux_tier_endpoint_from_payload(
    payload: dict[str, Any],
    *,
    tier,
    endpoint_model,
    tier_endpoint_model,
) -> tuple[Any | None, HttpResponseBadRequest | None]:
    endpoint = get_object_or_404(endpoint_model, pk=payload.get("endpoint_id"))
    if tier.tier_endpoints.filter(endpoint=endpoint).exists():
        return None, HttpResponseBadRequest("Endpoint already exists in tier")
    try:
        weight = float(payload.get("weight", 1))
    except (TypeError, ValueError):
        return None, HttpResponseBadRequest("weight must be numeric")
    if weight <= 0:
        return None, HttpResponseBadRequest("weight must be greater than zero")
    tier_endpoint = tier_endpoint_model.objects.create(tier=tier, endpoint=endpoint, weight=weight)
    return tier_endpoint, None


def _update_weighted_tier_endpoint_from_payload(
    tier_endpoint,
    payload: dict[str, Any],
) -> HttpResponseBadRequest | None:
    if "weight" in payload:
        try:
            weight = float(payload.get("weight"))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("weight must be numeric")
        if weight <= 0:
            return HttpResponseBadRequest("weight must be greater than zero")
        tier_endpoint.weight = weight
    tier_endpoint.save()
    return None


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


def _staff_user_display_name(user) -> str:
    full_name = user.get_full_name().strip()
    if full_name:
        return full_name
    if user.email:
        return user.email
    return user.get_username()


def _staff_user_admin_url(user) -> str:
    return reverse(f"admin:{user._meta.app_label}_{user._meta.model_name}_change", args=[user.pk])


def _staff_org_admin_url(org: Organization) -> str:
    return reverse("admin:api_organization_change", args=[org.pk])


def _serialize_staff_agent_summary(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "name": agent.name or "",
        "organizationName": agent.organization.name if agent.organization_id else None,
        "adminUrl": reverse("admin:api_persistentagent_change", args=[agent.id]),
        "developerChatUrl": build_staff_developer_chat_path_for_agent(agent),
        "lastInteractionAt": agent.last_interaction_at.isoformat() if agent.last_interaction_at else None,
    }


def _serialize_staff_agent_summaries(**filters) -> list[dict[str, Any]]:
    return [
        _serialize_staff_agent_summary(agent)
        for agent in (
            PersistentAgent.objects
            .non_eval()
            .alive()
            .filter(**filters)
            .select_related("organization")
            .order_by(models.F("last_interaction_at").desc(nulls_last=True), "-created_at")
        )
    ]


def _staff_user_scoped_agents(user):
    return PersistentAgent.objects.non_eval().alive().filter(user=user, organization__isnull=True)


def _staff_org_scoped_agents(org: Organization):
    return PersistentAgent.objects.non_eval().alive().filter(organization=org)


def _staff_stripe_customer_dashboard_url(customer) -> str | None:
    customer_id = getattr(customer, "id", "") or ""
    if not customer_id:
        return None
    live_mode = bool(getattr(customer, "livemode", settings.STRIPE_LIVE_MODE))
    base_url = "https://dashboard.stripe.com"
    if not live_mode:
        base_url = f"{base_url}/test"
    return f"{base_url}/customers/{customer_id}"


def _coerce_decimal_payload(value: Any, *, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return default


def _serialize_decimal(value: Decimal | int | float) -> str:
    return str(value)


def _serialize_staff_addon(entitlement: AddonEntitlement) -> dict[str, Any]:
    total_task_credits = entitlement.task_credits_delta * entitlement.quantity
    total_contacts = entitlement.contact_cap_delta * entitlement.quantity
    total_browser_tasks = entitlement.browser_task_daily_delta * entitlement.quantity
    total_captcha = entitlement.advanced_captcha_resolution_delta * entitlement.quantity

    if total_task_credits:
        kind = "task_pack"
        label = "Task Pack"
    elif total_contacts:
        kind = "contact_pack"
        label = "Contact Pack"
    elif total_browser_tasks:
        kind = "browser_task_pack"
        label = "Browser Task Pack"
    elif total_captcha:
        kind = "advanced_captcha"
        label = "Advanced CAPTCHA"
    else:
        kind = "addon"
        label = "Add-on"

    summary_parts: list[str] = []
    if total_task_credits:
        summary_parts.append(f"+{total_task_credits:g} task credits")
    if total_contacts:
        summary_parts.append(f"+{total_contacts} contacts")
    if total_browser_tasks:
        summary_parts.append(f"+{total_browser_tasks} browser tasks/day")
    if total_captcha:
        summary_parts.append("CAPTCHA solving enabled")

    return {
        "id": str(entitlement.id),
        "kind": kind,
        "label": label,
        "quantity": entitlement.quantity,
        "priceId": entitlement.price_id,
        "summary": ", ".join(summary_parts) or "Configured",
        "startsAt": entitlement.starts_at.isoformat() if entitlement.starts_at else None,
        "expiresAt": entitlement.expires_at.isoformat() if entitlement.expires_at else None,
        "isRecurring": bool(entitlement.is_recurring),
    }


def _serialize_task_credit(task_credit: TaskCredit) -> dict[str, Any]:
    return {
        "id": str(task_credit.id),
        "credits": _serialize_decimal(task_credit.credits),
        "used": _serialize_decimal(task_credit.credits_used),
        "available": _serialize_decimal(task_credit.available_credits),
        "grantType": task_credit.grant_type,
        "grantedAt": task_credit.granted_date.isoformat(),
        "expiresAt": task_credit.expiration_date.isoformat(),
        "comments": task_credit.comments or "",
    }


def _serialize_staff_task_credits(owner) -> dict[str, Any]:
    current_credits = TaskCreditService.get_current_task_credit_for_owner(owner)
    available_credits = TaskCreditService.calculate_available_tasks_for_owner(
        owner,
        task_credits=current_credits,
    )
    unlimited_credits = available_credits == Decimal(TASKS_UNLIMITED)
    recent_grants = [
        _serialize_task_credit(task_credit)
        for task_credit in current_credits.order_by("-granted_date", "-id")[:5]
    ]
    return {
        "available": None if unlimited_credits else _serialize_decimal(available_credits),
        "unlimited": bool(unlimited_credits),
        "recentGrants": recent_grants,
    }


def _serialize_staff_user_detail(user) -> dict[str, Any]:
    plan_payload = get_user_plan(user) or {}
    stripe_customer = get_stripe_customer(user)

    addons = [
        _serialize_staff_addon(entitlement)
        for entitlement in AddonEntitlement.objects.for_owner(user).active().order_by("-created_at")
    ]

    return {
        "user": {
            "id": user.id,
            "name": _staff_user_display_name(user),
            "email": user.email or "",
            "adminUrl": _staff_user_admin_url(user),
        },
        "emailVerification": _serialize_email_verification(user),
        "billing": {
            "plan": {
                "id": plan_payload.get("id") or PlanNamesChoices.FREE,
                "name": plan_payload.get("name") or "Free",
            },
            "stripeCustomerId": getattr(stripe_customer, "id", None),
            "stripeCustomerUrl": _staff_stripe_customer_dashboard_url(stripe_customer),
            "addons": addons,
        },
        "agents": _serialize_staff_agent_summaries(user=user),
        "taskCredits": _serialize_staff_task_credits(user),
        "userEmails": {
            "triggers": [
                _serialize_staff_user_email(user_email)
                for user_email in UserEmail.objects.filter(is_active=True).order_by("name", "event_name")
            ],
        },
    }


def _serialize_staff_org_member(membership: OrganizationMembership) -> dict[str, Any]:
    user = membership.user
    return {
        "userId": user.id,
        "name": _staff_user_display_name(user),
        "email": user.email or "",
        "role": membership.role,
        "roleLabel": membership.get_role_display(),
        "adminUrl": _staff_user_admin_url(user),
    }


def _serialize_staff_org_detail(org: Organization) -> dict[str, Any]:
    billing = getattr(org, "billing", None)
    members = [
        _serialize_staff_org_member(membership)
        for membership in (
            OrganizationMembership.objects
            .filter(org=org, status=OrganizationMembership.OrgStatus.ACTIVE)
            .select_related("user")
            .order_by("user__email", "user__id")
        )
    ]

    return {
        "organization": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "plan": org.plan,
            "isActive": bool(org.is_active),
            "adminUrl": _staff_org_admin_url(org),
            "createdAt": org.created_at.isoformat() if org.created_at else None,
        },
        "billing": {
            "subscription": billing.subscription if billing else None,
            "purchasedSeats": billing.purchased_seats if billing else None,
            "seatsReserved": billing.seats_reserved if billing else None,
            "seatsAvailable": billing.seats_available if billing else None,
        },
        "members": members,
        "agents": _serialize_staff_agent_summaries(organization=org),
        "taskCredits": _serialize_staff_task_credits(org),
    }


def _serialize_staff_user_email(user_email: UserEmail) -> dict[str, Any]:
    return {
        "id": user_email.id,
        "name": user_email.name,
        "eventName": user_email.event_name,
    }


class StaffUserSearchAPIView(SystemAdminAPIView):
    """Search users and organizations for staff account triage."""

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
            return JsonResponse({"users": [], "organizations": []})

        filters = (
            Q(email__icontains=query)
            | Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
        terms = [term for term in query.split() if term]
        if len(terms) >= 2:
            filters |= Q(first_name__icontains=terms[0], last_name__icontains=" ".join(terms[1:]))
            filters |= Q(first_name__icontains=" ".join(terms[:-1]), last_name__icontains=terms[-1])
        if query.isdigit():
            filters |= Q(id=int(query))

        matches = User.objects.filter(filters).order_by("first_name", "last_name", "email", "id")[:limit]
        user_payload = [
            {
                "id": user.id,
                "name": _staff_user_display_name(user),
                "email": user.email or "",
            }
            for user in matches
        ]

        org_filters = Q(name__icontains=query) | Q(slug__icontains=query)
        try:
            org_filters |= Q(id=uuid.UUID(query))
        except (TypeError, ValueError):
            pass

        org_matches = Organization.objects.filter(org_filters).order_by("name", "slug", "id")[:limit]
        org_payload = [
            {
                "id": str(org.id),
                "name": org.name,
                "slug": org.slug,
            }
            for org in org_matches
        ]
        return JsonResponse({"users": user_payload, "organizations": org_payload})


class StaffUserDetailAPIView(SystemAdminAPIView):
    """Return the full staff user-management payload for one user."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, user_id: int, *args: Any, **kwargs: Any):
        user = get_object_or_404(User, pk=user_id)
        return JsonResponse(_serialize_staff_user_detail(user))


class StaffOrgDetailAPIView(SystemAdminAPIView):
    """Return the staff organization-management payload for one organization."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, org_id: uuid.UUID, *args: Any, **kwargs: Any):
        org = get_object_or_404(Organization.objects.select_related("billing"), pk=org_id)
        return JsonResponse(_serialize_staff_org_detail(org))


class StaffUserEmailVerifyAPIView(SystemAdminAPIView):
    """Allow staff to manually mark a user's current email as verified."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, user_id: int, *args: Any, **kwargs: Any):
        user = get_object_or_404(User, pk=user_id)
        email = (user.email or "").strip()
        if not email:
            return JsonResponse({"error": "user_has_no_email"}, status=400)

        from allauth.account.models import EmailAddress

        with transaction.atomic():
            email_address = (
                EmailAddress.objects
                .select_for_update()
                .filter(user=user, email__iexact=email)
                .order_by("-primary", "-verified", "pk")
                .first()
            )
            if email_address is None:
                EmailAddress.objects.filter(user=user, primary=True).update(primary=False)
                email_address = EmailAddress.objects.create(
                    user=user,
                    email=email,
                    verified=True,
                    primary=True,
                )
            else:
                EmailAddress.objects.filter(user=user, primary=True).exclude(pk=email_address.pk).update(primary=False)
                updated_fields: list[str] = []
                if email_address.email != email:
                    email_address.email = email
                    updated_fields.append("email")
                if not email_address.verified:
                    email_address.verified = True
                    updated_fields.append("verified")
                if not email_address.primary:
                    email_address.primary = True
                    updated_fields.append("primary")
                if updated_fields:
                    email_address.save(update_fields=updated_fields)

            EmailAddress.objects.filter(user=user, email__iexact=email).exclude(pk=email_address.pk).update(
                verified=True,
                primary=False,
            )

        return JsonResponse(
            {
                "ok": True,
                "emailVerification": _serialize_email_verification(user),
            }
        )


def _parse_staff_task_credit_grant_payload(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "invalid_json"}, status=400)

    credits = _coerce_decimal_payload(payload.get("credits"))
    if not credits.is_finite():
        return None, JsonResponse({"error": "credits_must_be_finite"}, status=400)
    if credits <= Decimal("0"):
        return None, JsonResponse({"error": "credits_must_be_positive"}, status=400)

    grant_type = str(payload.get("grantType") or "").strip()
    if grant_type not in {GrantTypeChoices.COMPENSATION, GrantTypeChoices.PROMO}:
        return None, JsonResponse({"error": "invalid_grant_type"}, status=400)

    expiration_presets = {
        "one_month": relativedelta(months=1),
        "one_year": relativedelta(years=1),
    }
    expiration_preset = str(payload.get("expirationPreset") or "").strip()
    expiration_delta = expiration_presets.get(expiration_preset)
    if expiration_delta is None:
        return None, JsonResponse({"error": "invalid_expiration_preset"}, status=400)

    return {
        "credits": credits,
        "grant_type": grant_type,
        "expiration_delta": expiration_delta,
    }, None


def _create_staff_task_credit_grant(owner, payload: dict[str, Any]) -> TaskCredit:
    granted_at = timezone.now()
    fields: dict[str, Any] = {
        "credits": payload["credits"],
        "credits_used": Decimal("0"),
        "granted_date": granted_at,
        "expiration_date": granted_at + payload["expiration_delta"],
        "grant_type": payload["grant_type"],
        "additional_task": False,
        "voided": False,
    }
    if isinstance(owner, Organization):
        billing = getattr(owner, "billing", None)
        fields.update(organization=owner, plan=(billing.subscription if billing and billing.subscription else PlanNamesChoices.FREE))
    else:
        fields.update(user=owner, plan=PlanNamesChoices.FREE)
    return TaskCredit.objects.create(**fields)


def _staff_user_target(*, user_id: int, **kwargs: Any):
    return get_object_or_404(User, pk=user_id)


def _staff_org_target(*, org_id: uuid.UUID, **kwargs: Any):
    return get_object_or_404(Organization.objects.select_related("billing"), pk=org_id)


def _staff_task_credit_grant_response(request: HttpRequest, owner):
    payload, error_response = _parse_staff_task_credit_grant_payload(request)
    if error_response is not None:
        return error_response
    task_credit = _create_staff_task_credit_grant(owner, payload)
    return JsonResponse({"ok": True, "taskCredit": _serialize_task_credit(task_credit)}, status=201)


def _parse_staff_system_message_payload(request: HttpRequest) -> tuple[str | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "invalid_json"}, status=400)

    body = str(payload.get("body") or "").strip()
    if not body:
        return None, JsonResponse({"error": "body_required"}, status=400)
    return body, None


def _create_staff_scoped_system_messages(request: HttpRequest, target_qs) -> JsonResponse:
    body, error_response = _parse_staff_system_message_payload(request)
    if error_response is not None:
        return error_response

    agent_ids = list(target_qs.order_by("id").values_list("id", flat=True))
    target_count = len(agent_ids)
    if target_count == 0:
        return JsonResponse({"ok": True, "createdCount": 0, "targetCount": 0}, status=200)

    created_by = request.user if request.user.is_authenticated else None
    system_messages = [
        PersistentAgentSystemMessage(
            agent_id=agent_id,
            body=body,
            created_by=created_by,
            is_active=True,
        )
        for agent_id in agent_ids
    ]
    PersistentAgentSystemMessage.objects.bulk_create(system_messages, batch_size=500)
    return JsonResponse({"ok": True, "createdCount": target_count, "targetCount": target_count}, status=201)


def _queue_staff_scoped_process_events(target_qs) -> JsonResponse:
    agents = list(target_qs.order_by("id").only("id", "is_active"))
    skipped_inactive_count = 0
    active_agent_ids: list[str] = []

    for agent in agents:
        if not agent.is_active:
            skipped_inactive_count += 1
            continue
        active_agent_ids.append(str(agent.id))

    queued_count = len(active_agent_ids)
    if queued_count:
        try:
            queue_agent_process_events_batch_task.delay(active_agent_ids)
        except (CeleryError, KombuOperationalError) as exc:
            logger.exception("Failed to queue scoped process events batch for staff action")
            return JsonResponse(
                {
                    "error": "queue_failed",
                    "detail": str(exc),
                    "queuedCount": 0,
                    "skippedInactiveCount": skipped_inactive_count,
                    "targetCount": len(agents),
                },
                status=500,
            )

    status_code = 202 if queued_count else 200
    return JsonResponse(
        {
            "ok": True,
            "queuedCount": queued_count,
            "skippedInactiveCount": skipped_inactive_count,
            "targetCount": len(agents),
        },
        status=status_code,
    )


def _staff_system_message_response(request: HttpRequest, owner):
    scoped_agents = _staff_org_scoped_agents(owner) if isinstance(owner, Organization) else _staff_user_scoped_agents(owner)
    return _create_staff_scoped_system_messages(request, scoped_agents)


def _staff_process_events_response(request: HttpRequest, owner):
    scoped_agents = _staff_org_scoped_agents(owner) if isinstance(owner, Organization) else _staff_user_scoped_agents(owner)
    return _queue_staff_scoped_process_events(scoped_agents)


class _StaffOwnerPostAPIView(SystemAdminAPIView):
    http_method_names = ["post"]
    target_resolver = staticmethod(lambda **kwargs: None)
    response_builder = staticmethod(lambda request, owner: None)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return self.response_builder(request, self.target_resolver(**kwargs))


for _name, _target_resolver, _response_builder in (
    ("StaffUserTaskCreditGrantAPIView", _staff_user_target, _staff_task_credit_grant_response),
    ("StaffOrgTaskCreditGrantAPIView", _staff_org_target, _staff_task_credit_grant_response),
    ("StaffUserSystemMessageAPIView", _staff_user_target, _staff_system_message_response),
    ("StaffOrgSystemMessageAPIView", _staff_org_target, _staff_system_message_response),
    ("StaffUserProcessEventsAPIView", _staff_user_target, _staff_process_events_response),
    ("StaffOrgProcessEventsAPIView", _staff_org_target, _staff_process_events_response),
):
    globals()[_name] = type(
        _name,
        (_StaffOwnerPostAPIView,),
        {
            "__module__": __name__,
            "target_resolver": staticmethod(_target_resolver),
            "response_builder": staticmethod(_response_builder),
        },
    )
del _name, _target_resolver, _response_builder


class StaffUserEmailTriggerAPIView(SystemAdminAPIView):
    """Send a configured analytics event for the selected user."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, user_id: int, user_email_id: int, *args: Any, **kwargs: Any):
        user = get_object_or_404(User, pk=user_id)
        user_email = get_object_or_404(UserEmail, pk=user_email_id, is_active=True)

        properties = {
            "medium": str(AnalyticsSource.CONSOLE),
            "triggered_from": "staff_user_page",
            "user_email_trigger_id": str(user_email.id),
            "user_email_trigger_name": user_email.name,
            "triggered_by_staff_user_id": str(request.user.id),
            "triggered_by_staff_user_email": request.user.email or "",
            "target_user_id": str(user.id),
            "target_user_email": user.email or "",
        }
        Analytics.track(
            user_id=user.id,
            event=user_email.event_name,
            properties=properties,
        )

        return JsonResponse(
            {
                "ok": True,
                "userEmail": _serialize_staff_user_email(user_email),
            }
        )


class StaffAgentDeveloperExportAPIView(SystemAdminAPIView):
    """Build and return a downloadable debugging export for Developer Mode."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        try:
            export_range = build_audit_export_range(request.GET.get("range"))
        except InvalidAuditExportRange as exc:
            return HttpResponseBadRequest(str(exc))

        audit_json_file = tempfile.SpooledTemporaryFile(mode="w+b", max_size=5 * 1024 * 1024)
        audit_summary = write_agent_audit_export_json(agent, audit_json_file, export_range=export_range)
        audit_json_file.seek(0)

        html = render_to_string(
            "console/staff_agent_audit_export.html",
            {
                "agent_name": agent.name or "Agent",
                "generated_at": audit_summary.get("exported_at"),
                "range_label": (audit_summary.get("range") or {}).get("label"),
            },
        )
        viewer_js = render_to_string("console/staff_agent_audit_export_viewer.js")

        archive_file = tempfile.SpooledTemporaryFile(mode="w+b", max_size=10 * 1024 * 1024)
        with zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("index.html", html.encode("utf-8"))
            archive.writestr("viewer.js", viewer_js.encode("utf-8"))
            with archive.open("audit-data.json", "w") as zipped_json:
                audit_json_file.seek(0)
                shutil.copyfileobj(audit_json_file, zipped_json, length=64 * 1024)
            with archive.open("audit-data.js", "w") as zipped_js:
                zipped_js.write(b"window.__AUDIT_DATA__=")
                audit_json_file.seek(0)
                shutil.copyfileobj(audit_json_file, zipped_js, length=64 * 1024)
                zipped_js.write(b";")
        archive_file.seek(0)

        timestamp_label = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        base_name = get_valid_filename(agent.name or "") or f"agent_{agent.id}"
        filename = f"{base_name}_audit_export_{timestamp_label}.zip"

        return FileResponse(
            archive_file,
            as_attachment=True,
            filename=filename,
            content_type="application/zip",
        )


class StaffAgentProcessEventsAPIView(SystemAdminAPIView):
    """Staff-only hook to enqueue a PROCESS_EVENTS run for an agent."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        if not agent.is_active:
            processing_active = compute_processing_status(agent)
            return JsonResponse({"queued": False, "processing_active": processing_active}, status=202)
        try:
            process_agent_events_task.delay(str(agent.id))
            queued = True
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to queue process events for agent %s", agent.id)
            return JsonResponse({"error": "queue_failed", "detail": str(exc)}, status=500)

        processing_active = compute_processing_status(agent)
        return JsonResponse({"queued": queued, "processing_active": processing_active}, status=202)


class StaffAgentRunJudgeAPIView(SystemAdminAPIView):
    """Staff-only hook to run the advisory judge immediately for an agent."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        try:
            result = run_manual_agent_judge(agent)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to run manual judge for agent %s", agent.id)
            return JsonResponse({"error": "judge_failed", "detail": str(exc)}, status=500)

        suggestion = result.get("suggestion")
        if isinstance(suggestion, dict) and suggestion.get("suggestionId"):
            suggestion["decisionApiUrl"] = reverse(
                "console_agent_developer_judge_suggestion_decision",
                kwargs={"agent_id": agent.id, "suggestion_id": suggestion["suggestionId"]},
            )
        return JsonResponse(result, status=200)


class StaffAgentJudgeSuggestionDecisionAPIView(SystemAdminAPIView):
    """Approve or reject a staff-reviewed manual judge suggestion."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, suggestion_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        suggestion = get_object_or_404(
            PersistentAgentJudgeSuggestion.objects,
            id=suggestion_id,
            agent=agent,
        )
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        decision = str(payload.get("decision") or "").strip().lower()
        if decision == "approve":
            approve_judge_suggestion(suggestion)
        elif decision == "reject":
            dismiss_judge_suggestion(suggestion)
        else:
            return HttpResponseBadRequest("decision must be approve or reject")

        suggestion.refresh_from_db()
        return JsonResponse(
            {
                "status": suggestion.status,
            },
            status=200,
        )


class StaffAgentSystemMessageAPIView(SystemAdminAPIView):
    """Create a per-agent system directive for staff audit UI."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        body = (payload.get("body") or "").strip()
        if not body:
            return HttpResponseBadRequest("body is required")

        message = PersistentAgentSystemMessage.objects.create(
            agent=agent,
            body=body,
            created_by=request.user if request.user.is_authenticated else None,
        )

        return JsonResponse(serialize_system_message(message), status=201)


class StaffAgentSystemMessageDetailAPIView(SystemAdminAPIView):
    """Update an existing system directive from the staff audit UI."""

    http_method_names = ["patch"]

    def patch(self, request: HttpRequest, agent_id: str, message_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        message = get_object_or_404(PersistentAgentSystemMessage, pk=message_id, agent=agent)

        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        updates: list[str] = []

        if "body" in payload:
            body = (payload.get("body") or "").strip()
            if not body:
                return HttpResponseBadRequest("body cannot be blank")
            if message.body != body:
                message.body = body
                updates.append("body")

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



def _web_chat_properties(agent: PersistentAgent, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return analytics properties annotated with agent + organization context."""

    payload: dict[str, Any] = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
    }
    if extra:
        payload.update(extra)

    return Analytics.with_org_properties(payload, organization=getattr(agent, "organization", None))


def _serialize_agent_profile_payload(
    request: HttpRequest,
    agent: PersistentAgent,
    *,
    owner=None,
    is_collaborator: bool | None = None,
    processing_active: bool | None = None,
    pending_action_count: int | None = None,
    message_read_state: dict | None = None,
    org_ids: set | None = None,
    admin_org_ids: set | None = None,
    is_admin_user: bool | None = None,
    enrich: bool = False,
) -> dict[str, Any]:
    user = request.user
    if enrich:
        enrich_agents_for_card_surface([agent], owner or agent.organization or agent.user)
    if is_collaborator is None:
        is_collaborator = user_is_collaborator(user, agent)
    if processing_active is None:
        processing_active = build_processing_activity_map([agent]).get(str(agent.id), False)
    if pending_action_count is None:
        pending_action_count = count_pending_action_requests_for_agents([agent], user).get(str(agent.id), 0)
    if message_read_state is None:
        message_read_state = build_latest_agent_message_read_state([agent.id], user).get(str(agent.id), {})
    if org_ids is None or admin_org_ids is None:
        memberships = OrganizationMembership.objects.filter(
            user=user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        if org_ids is None:
            org_ids = set(memberships.values_list("org_id", flat=True))
        if admin_org_ids is None:
            admin_org_ids = set(
                memberships.filter(role__in=BILLING_MANAGE_ROLES).values_list("org_id", flat=True)
            )
    if is_admin_user is None:
        is_admin_user = bool(user.is_staff or user.is_superuser)

    card_payload = serialize_agent_card_payload(
        request,
        agent,
        avatar_variant="thumbnail",
        is_staff=is_admin_user,
        is_shared=is_collaborator,
    )
    enabled_skill_states = getattr(agent, "enabled_system_skill_states_for_roster", None)
    if enabled_skill_states is None:
        enabled_skill_states = agent.system_skill_states.filter(is_enabled=True).order_by("skill_key")

    return {
        "id": str(agent.id),
        "name": agent.name or "",
        "avatar_url": card_payload["avatarUrl"],
        "is_active": bool(agent.is_active),
        "processing_active": bool(processing_active),
        "mini_description": agent.mini_description or "",
        "short_description": agent.short_description or "",
        "listing_description": card_payload["listingDescription"],
        "listing_description_source": card_payload["listingDescriptionSource"],
        "display_tags": card_payload["displayTags"],
        "detail_url": card_payload["detailUrl"],
        "daily_credit_remaining": card_payload["dailyCreditRemaining"],
        "daily_credit_low": card_payload["dailyCreditLow"],
        "last_24h_credit_burn": card_payload["last24hCreditBurn"],
        "is_org_owned": agent.organization_id is not None,
        "is_collaborator": bool(is_collaborator),
        "can_manage_agent": bool(
            is_admin_user
            or agent.user_id == user.id
            or (agent.organization_id and agent.organization_id in org_ids)
        ),
        "can_manage_collaborators": bool(
            is_admin_user
            or agent.user_id == user.id
            or (agent.organization_id and agent.organization_id in admin_org_ids)
        ),
        "can_send_messages": user_has_natural_agent_chat_access(user, agent),
        "developer_live_chat_url": card_payload["developerChatUrl"],
        "preferred_llm_tier": getattr(getattr(agent, "preferred_llm_tier", None), "key", None),
        "email": card_payload["primaryEmail"],
        "sms": card_payload["primarySms"],
        "last_interaction_at": agent.last_interaction_at.isoformat() if agent.last_interaction_at else None,
        "signup_preview_state": agent.signup_preview_state,
        "planning_state": agent.planning_state,
        "pending_action_request_count": pending_action_count,
        "enabled_system_skills": [
            state.skill_key
            for state in enabled_skill_states
            if state.skill_key
        ],
        **serialize_latest_agent_message_read_state(message_read_state),
    }


def _build_agent_critical_status_payload(request: HttpRequest, agent: PersistentAgent) -> dict[str, Any]:
    owner = agent.organization or agent.user
    quick_settings = build_agent_quick_settings_payload(agent, owner)
    quick_meta = quick_settings.get("meta") or {}
    quick_plan = quick_meta.get("plan") or {}
    plan_payload = (
        get_organization_plan(agent.organization)
        if agent.organization_id
        else reconcile_user_plan_from_stripe(agent.user)
    )
    addons = build_agent_addons_payload(
        agent,
        owner,
        can_open_billing=_can_open_agent_billing(request, agent),
    )
    status = addons["status"]
    return {
        "billing": status["billing"],
        "accountPause": status["accountPause"],
        "dailyCredits": (quick_settings.get("status") or {}).get("dailyCredits"),
        "contactCap": addons["contactCap"],
        "contactCapStatus": status["contactCap"],
        "hardLimit": {
            "showUpsell": bool(quick_plan.get("isFree")),
            "upgradeUrl": quick_meta.get("upgradeUrl"),
        },
        "canManageAddons": _can_manage_contact_packs(request, agent, plan_payload),
        "manageBillingUrl": addons["manageBillingUrl"],
    }


def _serialize_roster_agent_invite(
    invite: AgentTransferInvite | AgentCollaboratorInvite,
) -> dict[str, Any]:
    agent = invite.agent
    is_transfer = isinstance(invite, AgentTransferInvite)
    sender = invite.initiated_by if is_transfer else invite.invited_by
    sender_email = sender.email or ""
    sender_name = sender.get_full_name() or sender_email or sender.username
    route_prefix = "console-agent-transfer-invite" if is_transfer else "console-agent-collaborator-invite"
    route_arg = invite.id if is_transfer else invite.token

    return {
        "id": str(invite.id),
        "kind": "transfer" if is_transfer else "collaboration",
        "agent_name": agent.name or "Agent",
        "agent_avatar_url": agent.get_avatar_thumbnail_url(),
        "sender_name": sender_name or "Gobii user",
        "sender_email": sender_email,
        "message": (invite.message or "") if is_transfer else "",
        "accept_url": reverse(f"{route_prefix}-accept-api", args=[route_arg]),
        "decline_url": reverse(f"{route_prefix}-decline-api", args=[route_arg]),
    }


def _pending_roster_agent_invites_for_user(request: HttpRequest) -> list[dict[str, Any]]:
    from allauth.account.models import EmailAddress

    primary_email = (request.user.email or "").strip().lower()
    transfer_invites = (
        AgentTransferInvite.objects
        .select_related("agent", "initiated_by")
        .filter(
            to_email__iexact=primary_email,
            status=AgentTransferInvite.Status.PENDING,
        )
        if primary_email else AgentTransferInvite.objects.none()
    )
    recipient_emails = {
        email.lower()
        for email in EmailAddress.objects.filter(user=request.user, verified=True)
        .values_list("email", flat=True)
    }
    collaboration_invites = (
        AgentCollaboratorInvite.objects
        .select_related("agent", "invited_by")
        .filter(
            email__in=recipient_emails,
            status=AgentCollaboratorInvite.InviteStatus.PENDING,
            expires_at__gt=timezone.now(),
        )
    )
    invites = [*transfer_invites, *collaboration_invites]
    invites.sort(key=lambda invite: (invite.created_at, str(invite.id)), reverse=True)
    return [_serialize_roster_agent_invite(invite) for invite in invites]


@method_decorator(csrf_exempt, name="dispatch")
class AgentChatRosterAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def _resolve_override_for_agent(
        self,
        request: HttpRequest,
        agent_id: str,
    ) -> tuple[dict[str, str] | None, JsonResponse | None, str | None]:
        override, error_code = resolve_context_override_for_agent(
            request.user,
            agent_id,
            include_deleted=True,
        )
        if error_code is None:
            return override, None, None
        if error_code == "not_found":
            return None, None, "missing"
        if error_code == "forbidden":
            return None, JsonResponse({"error": "Not permitted"}, status=403), None
        if error_code == "deleted":
            return override, None, "deleted"
        return None, None, "missing"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        override = get_context_override(request)
        staff_override = get_staff_context_override(request)
        if staff_override and not (request.user.is_staff or request.user.is_superuser):
            return JsonResponse({"error": "Not permitted"}, status=403)
        for_agent_id = request.GET.get("for_agent")
        requested_agent_status = None
        resolved_preferences = UserPreference.resolve_known_preferences(request.user)
        agent_roster_sort_mode = resolved_preferences.get(UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE)
        favorite_agent_ids = resolved_preferences.get(
            UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
            [],
        )
        muted_agent_ids = resolved_preferences.get(
            UserPreference.KEY_AGENT_CHAT_MUTED_AGENT_IDS,
            [],
        )
        insights_panel_expanded = resolved_preferences.get(
            UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED
        )
        agent_chat_notifications_enabled = resolved_preferences.get(
            UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED
        )
        if for_agent_id and not staff_override:
            override_for_agent, error_response, requested_agent_status = self._resolve_override_for_agent(
                request,
                for_agent_id,
            )
            if error_response:
                return error_response
            if override_for_agent is not None:
                override = override_for_agent

        try:
            context_info = (
                resolve_staff_console_context(request.user, staff_override)
                if staff_override
                else resolve_console_context(request.user, request.session, override=override)
            )
        except PermissionDenied:
            return JsonResponse({"error": "Not permitted"}, status=403)

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
        elif staff_override:
            owner = User.objects.filter(id=context_info.current_context.id).first()
            if owner is None:
                return JsonResponse({"error": "Not permitted"}, status=403)

        llm_intelligence = build_llm_intelligence_props(
            owner,
            owner_type,
            organization,
            upgrade_url,
        )

        # Prefetch email endpoints and prefer primary first when available.
        email_prefetch = models.Prefetch(
            "comms_endpoints",
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.EMAIL).order_by("-is_primary", "address"),
            to_attr="email_endpoints_for_display",
        )
        sms_prefetch = models.Prefetch(
            "comms_endpoints",
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.SMS),
            to_attr="primary_sms_endpoints",
        )
        enabled_system_skills_prefetch = models.Prefetch(
            "system_skill_states",
            queryset=PersistentAgentSystemSkillState.objects.filter(is_enabled=True).order_by("skill_key"),
            to_attr="enabled_system_skill_states_for_roster",
        )
        if staff_override:
            agents_qs = PersistentAgent.objects.non_eval().alive().select_related("browser_use_agent")
            if context_info.current_context.type == "organization":
                agents_qs = agents_qs.filter(organization_id=context_info.current_context.id)
            else:
                agents_qs = agents_qs.filter(
                    organization__isnull=True,
                    user_id=context_info.current_context.id,
                )
            agents_qs = agents_qs.prefetch_related(
                email_prefetch,
                sms_prefetch,
                enabled_system_skills_prefetch,
            ).order_by("name")
        else:
            agents_qs = (
                agent_queryset_for(
                    request.user,
                    context_info.current_context,
                    allow_delinquent_personal_chat=True,
                )
                .prefetch_related(email_prefetch, sms_prefetch, enabled_system_skills_prefetch)
                .order_by("name")
            )
        agent_ids = list(agents_qs.values_list("id", flat=True))
        agents = list(agents_qs)
        if context_info.current_context.type == "personal" and not staff_override:
            shared_qs = (
                shared_agent_queryset_for(request.user)
                .prefetch_related(email_prefetch, sms_prefetch, enabled_system_skills_prefetch)
            )
            if agent_ids:
                shared_qs = shared_qs.exclude(id__in=agent_ids)
            shared_agents = list(shared_qs.order_by("name"))
        else:
            shared_agents = []
        collaborators_by_agent_id = {agent.id for agent in shared_agents}
        agents += shared_agents
        if staff_override and for_agent_id and all(str(agent.id) != str(for_agent_id) for agent in agents):
            requested_agent = (
                PersistentAgent.objects
                .select_related("browser_use_agent")
                .prefetch_related(email_prefetch, sms_prefetch, enabled_system_skills_prefetch)
                .filter(id=for_agent_id)
                .first()
            )
            if requested_agent is not None:
                matches_context = (
                    str(requested_agent.organization_id) == context_info.current_context.id
                    if context_info.current_context.type == "organization"
                    else requested_agent.organization_id is None
                    and str(requested_agent.user_id) == context_info.current_context.id
                )
                if matches_context:
                    agents.append(requested_agent)
        enrich_agents_for_card_surface(agents, owner)
        user = request.user
        processing_activity_by_agent_id = build_processing_activity_map(agents)
        pending_action_counts_by_agent_id = count_pending_action_requests_for_agents(agents, user)
        message_read_state_by_agent_id = build_latest_agent_message_read_state(
            (agent.id for agent in agents),
            request.user,
        )
        org_memberships = OrganizationMembership.objects.filter(
            user=user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        billing_manage_org_ids = set(
            org_memberships.filter(role__in=BILLING_MANAGE_ROLES).values_list("org_id", flat=True)
        )
        can_open_billing = bool(
            owner_type == "user"
            or (organization is not None and organization.id in billing_manage_org_ids)
        )
        manage_billing_url = None
        if can_open_billing:
            manage_billing_url = f"{IMMERSIVE_APP_BASE_PATH}/billing"
            if organization is not None:
                manage_billing_url = append_context_query(manage_billing_url, str(organization.id))
        billing_status = _build_billing_status_payload(
            owner,
            owner_type,
            can_open_billing=can_open_billing,
            manage_billing_url=manage_billing_url,
        )
        account_pause = build_account_pause_payload(
            owner,
            manage_billing_url=manage_billing_url,
        )
        org_ids = set(org_memberships.values_list("org_id", flat=True))
        admin_org_ids = set(
            org_memberships.filter(
                role__in=[
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
                ]
            ).values_list("org_id", flat=True)
        )
        # Keep behavior aligned with SystemAdminRequiredMixin: superusers may not be staff.
        is_admin_user = bool(user.is_staff or user.is_superuser)
        payload = []
        for agent in agents:
            is_collaborator = agent.id in collaborators_by_agent_id
            payload.append(
                _serialize_agent_profile_payload(
                    request,
                    agent,
                    owner=owner,
                    is_collaborator=is_collaborator,
                    processing_active=processing_activity_by_agent_id.get(str(agent.id), False),
                    pending_action_count=pending_action_counts_by_agent_id.get(str(agent.id), 0),
                    message_read_state=message_read_state_by_agent_id.get(str(agent.id), {}),
                    org_ids=org_ids,
                    admin_org_ids=admin_org_ids,
                    is_admin_user=is_admin_user,
                )
            )
        return JsonResponse(
            {
                "context": {
                    "type": context_info.current_context.type,
                    "id": context_info.current_context.id,
                    "name": context_info.current_context.name,
                    "canCreateAgents": context_info.can_create_org_agents,
                    "isStaffView": bool(staff_override),
                    "personalSignupPreviewCreateAvailable": _personal_signup_preview_create_available(
                        request,
                        context_info,
                    ),
                },
                "requested_agent_status": requested_agent_status,
                "agent_roster_sort_mode": agent_roster_sort_mode,
                "favorite_agent_ids": favorite_agent_ids,
                "muted_agent_ids": muted_agent_ids,
                "insights_panel_expanded": insights_panel_expanded,
                "agent_chat_notifications_enabled": agent_chat_notifications_enabled,
                "billingStatus": billing_status,
                "accountPause": account_pause,
                "agents": payload,
                "agent_invites": _pending_roster_agent_invites_for_user(request),
                "llmIntelligence": llm_intelligence,
            }
        )


class AgentProfileAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        return JsonResponse(
            _serialize_agent_profile_payload(
                request,
                agent,
                enrich=True,
            )
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentQuickCreateAPIView(LoginRequiredMixin, View):
    """API endpoint to create an agent from an initial message and return the agent ID."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        from console.agent_creation import create_persistent_agent_from_charter

        attachments: list[Any] = []
        if request.content_type and request.content_type.startswith("multipart/form-data"):
            try:
                body = request.POST
                attachments = list(request.FILES.getlist("attachments"))
            except (MultiPartParserError, RequestDataTooBig):
                max_size_label = filesizeformat(get_max_file_size() or 0).replace("\xa0", " ")
                return JsonResponse(
                    {"error": f"Upload is too large. Max file size is {max_size_label}."},
                    status=400,
                )
            oversize_error = _validate_console_chat_attachments(attachments)
            if oversize_error is not None:
                return JsonResponse({"error": oversize_error}, status=400)
            selected_pipedream_app_slugs_raw = body.getlist("selected_pipedream_app_slugs")
        else:
            try:
                body = json.loads(request.body or "{}")
            except json.JSONDecodeError:
                return HttpResponseBadRequest("Invalid JSON body")
            selected_pipedream_app_slugs_raw = body.get("selected_pipedream_app_slugs")

        template_code = (body.get("template_code") or body.get("templateCode") or "").strip()
        template_id = (body.get("template_id") or body.get("templateId") or "").strip()
        template_source = (body.get("template_source") or body.get("templateSource") or "").strip()
        template, resolved_template_source, template_error = _resolve_quick_create_template(
            request,
            template_source=template_source,
            template_id=template_id,
            template_code=template_code,
        )
        if template_error is not None:
            return template_error

        initial_message = (body.get("message") or "").strip()
        if template is not None:
            initial_message = (template.charter or "").strip()
        if not initial_message:
            return JsonResponse({"error": "Message is required"}, status=400)
        preferred_llm_tier_key = (body.get("preferred_llm_tier") or "").strip() or None
        charter_override = (body.get("charter_override") or "").strip() or None
        preferred_contact_method = (body.get("preferred_contact_method") or "web").strip().lower()
        if preferred_contact_method not in {"email", "web"}:
            return JsonResponse({"error": "Preferred contact method must be 'email' or 'web'."}, status=400)
        try:
            selected_pipedream_app_slugs = normalize_app_slugs(
                selected_pipedream_app_slugs_raw,
                strict=True,
                require_list=True,
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        owner = _resolve_request_context_owner(request)
        if owner is not None and bool(get_owner_account_pause_state(owner).get("customer_paused")):
            return JsonResponse({"error": _customer_account_pause_block_message(owner)}, status=403)

        contact_email = (request.user.email or "").strip()
        if preferred_contact_method == "email" and not contact_email:
            preferred_contact_method = "web"

        if template is not None and resolved_template_source == AGENT_TEMPLATE_SOURCE_PUBLIC_TEMPLATE:
            stage_agent_template_session(
                request,
                template,
                template_source=AGENT_TEMPLATE_SOURCE_PUBLIC_TEMPLATE,
            )
        elif template is not None and resolved_template_source == AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE:
            stage_agent_template_session(
                request,
                template,
                template_source=AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE,
                organization=template.organization,
            )

        try:
            result = create_persistent_agent_from_charter(
                request,
                initial_message=initial_message,
                contact_email=contact_email,
                email_enabled=bool(contact_email),
                sms_enabled=False,
                preferred_contact_method=preferred_contact_method,
                web_enabled=preferred_contact_method == "web",
                preferred_llm_tier_key=preferred_llm_tier_key,
                charter_override=charter_override,
                selected_pipedream_app_slugs=selected_pipedream_app_slugs,
                initial_attachments=attachments,
            )
        except PermissionDenied:
            return JsonResponse({"error": "Invalid context override."}, status=403)
        except TrialRequiredValidationError:
            _persist_quick_create_draft(
                request,
                initial_message=initial_message,
                preferred_llm_tier_key=preferred_llm_tier_key,
                charter_override=charter_override,
                selected_pipedream_app_slugs=selected_pipedream_app_slugs,
            )
            set_trial_onboarding_intent(
                request,
                target=TRIAL_ONBOARDING_TARGET_AGENT_UI,
            )
            set_trial_onboarding_requires_plan_selection(request, required=True)
            return JsonResponse(
                {
                    "error": PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE,
                    "onboarding_target": TRIAL_ONBOARDING_TARGET_AGENT_UI,
                    "requires_plan_selection": True,
                },
                status=400,
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

        agent_profile = _serialize_agent_profile_payload(
            request,
            result.agent,
            enrich=True,
        )

        return JsonResponse({
            "agent_id": str(result.agent.id),
            "agent_name": result.agent.name,
            "agent_email": agent_email,
            "planning_state": result.agent.planning_state,
            "agent": agent_profile,
        })


class UserPhoneAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post", "delete"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse(serialize_phone_state(request.user))

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        raw_number = (body.get("phone_number") or body.get("phoneNumber") or body.get("phone") or "").strip()
        if not raw_number:
            return JsonResponse({"error": "Phone number is required."}, status=400)

        from util.phone import validate_and_format_e164

        try:
            phone_formatted = validate_and_format_e164(raw_number)
        except ValidationError as exc:
            if getattr(exc, "code", None) == "unsupported_region":
                return JsonResponse({"error": "Phone numbers from this country are not yet supported."}, status=400)
            return JsonResponse({"error": "Enter a valid phone number."}, status=400)

        primary_phone = get_primary_phone(request.user)
        if primary_phone and primary_phone.phone_number == phone_formatted:
            return JsonResponse(serialize_phone_state(request.user))

        existing_pending = get_pending_phone(request.user)
        if existing_pending and existing_pending.phone_number == phone_formatted:
            return JsonResponse(serialize_phone_state(request.user))

        phone = None
        try:
            phone = UserPhoneNumber.objects.create(
                user=request.user,
                phone_number=phone_formatted,
                is_verified=False,
                is_primary=primary_phone is None and existing_pending is None,
                verified_at=None,
            )
            send_phone_verification(phone)
            if existing_pending:
                existing_pending.delete()
                if primary_phone is None:
                    phone.is_primary = True
                    phone.save(update_fields=["is_primary", "updated_at"])
        except IntegrityError:
            return JsonResponse({"error": "This phone number is already in use."}, status=400)
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to add phone number."
            return JsonResponse({"error": message_text}, status=400)
        except PhoneVerificationSendError:
            if phone is not None:
                phone.delete()
            return JsonResponse({"error": "Unable to send verification code. Please try again."}, status=400)

        return JsonResponse(serialize_phone_state(request.user))

    def delete(self, request: HttpRequest, *args: Any, **kwargs: Any):
        phone = get_primary_phone(request.user)
        if phone:
            phone.delete()
        pending_phone = get_pending_phone(request.user)
        if pending_phone:
            pending_phone.delete()
        return JsonResponse(serialize_phone_state(request.user))


class UserPhoneCancelAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        pending_phone = get_pending_phone(request.user)
        if not pending_phone:
            return JsonResponse(serialize_phone_state(request.user))

        remaining = get_phone_cooldown_remaining(pending_phone)
        if remaining > 0:
            return JsonResponse(
                {
                    **serialize_phone_state(request.user),
                    "error": "Please wait before trying another phone number.",
                },
                status=400,
            )

        pending_phone.delete()
        return JsonResponse(serialize_phone_state(request.user))


def _manageable_agents_using_endpoint_for_user(user, endpoint):
    agents = PersistentAgent.objects.filter(preferred_contact_endpoint=endpoint)
    if user.is_staff:
        return agents
    return agents.filter(
        Q(user=user, organization__isnull=True)
        | Q(
            organization__organizationmembership__user=user,
            organization__organizationmembership__status=OrganizationMembership.OrgStatus.ACTIVE,
        )
    ).distinct()


class UserPhoneVerifyAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        phone = get_pending_phone(request.user) or get_primary_phone(request.user)
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
            with transaction.atomic():
                verified_phone = form.save()
                if not verified_phone.is_primary:
                    old_primary_phone = get_primary_phone(request.user)
                    old_sms_endpoint = None
                    if old_primary_phone:
                        old_sms_endpoint = PersistentAgentCommsEndpoint.objects.filter(
                            channel=CommsChannel.SMS,
                            address__iexact=old_primary_phone.phone_number,
                            owner_agent__isnull=True,
                        ).first()
                        old_primary_phone.is_primary = False
                        old_primary_phone.save(update_fields=["is_primary", "updated_at"])

                    verified_phone.is_primary = True
                    verified_phone.save(update_fields=["is_primary", "updated_at"])

                    if old_sms_endpoint:
                        new_sms_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                            channel=CommsChannel.SMS,
                            address=verified_phone.phone_number,
                            defaults={"owner_agent": None},
                        )
                        manageable_agent_ids = _manageable_agents_using_endpoint_for_user(
                            request.user,
                            old_sms_endpoint,
                        ).values("id")
                        PersistentAgent.objects.filter(id__in=manageable_agent_ids).update(
                            preferred_contact_endpoint=new_sms_endpoint,
                        )

                    if old_primary_phone:
                        old_primary_phone.delete()
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to verify code."
            return JsonResponse({"error": message_text}, status=400)

        return JsonResponse(serialize_phone_state(request.user))


class UserPhoneResendAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        phone = get_pending_phone(request.user) or get_primary_phone(request.user)
        if not phone:
            return JsonResponse({"error": "Add a phone number first."}, status=400)
        if phone.is_verified:
            return JsonResponse(serialize_phone_state(request.user))

        remaining = get_phone_cooldown_remaining(phone)
        if remaining == 0:
            try:
                send_phone_verification(phone)
            except PhoneVerificationSendError:
                return JsonResponse({"error": "Unable to send verification code. Please try again."}, status=400)

        return JsonResponse(serialize_phone_state(request.user))


def _email_send_error_response(exc, user_id: int) -> JsonResponse:
    if isinstance(exc, ImmediateHttpResponse):
        if exc.response.status_code == 429:
            return JsonResponse(
                {"error": "Too many verification email requests. Please try again later."},
                status=429,
            )
        raise exc
    logger.exception("Failed to send email verification for user %s", user_id)
    return JsonResponse(
        {"error": "Failed to send verification email. Please try again later."},
        status=500,
    )


class UserEmailAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post", "put", "delete"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        unknown_fields = sorted(key for key in payload if key != "email")
        if unknown_fields:
            return JsonResponse(
                {"errors": {"nonFieldErrors": [f"Unsupported fields: {', '.join(unknown_fields)}"]}},
                status=400,
            )

        raw_email = payload.get("email")
        if not isinstance(raw_email, str):
            return JsonResponse({"errors": {"email": ["Enter a valid email address."]}}, status=400)

        form, email = validate_email_change(request.user, raw_email)
        if email is None:
            return JsonResponse(
                {
                    "errors": {
                        field: [str(error) for error in errors]
                        for field, errors in form.errors.items()
                    }
                },
                status=400,
            )

        try:
            email_verification = start_email_change(request, email)
        except (ImmediateHttpResponse, AnymailError, OSError, SMTPException) as exc:
            return _email_send_error_response(exc, request.user.id)
        except IntegrityError:
            return JsonResponse(
                {"errors": {"email": ["This email address is already associated with an account."]}},
                status=400,
            )

        return JsonResponse({"emailVerification": email_verification})

    def put(self, request: HttpRequest, *args: Any, **kwargs: Any):
        email_address = get_email_verification_target(request.user)
        if not email_address:
            return JsonResponse({"error": "No email address found."}, status=400)

        if not email_address.verified:
            try:
                send_email_verification(
                    request,
                    email_address,
                    redirect_url=EMAIL_CHANGE_REDIRECT_URL,
                )
            except (ImmediateHttpResponse, AnymailError, OSError, SMTPException) as exc:
                return _email_send_error_response(exc, request.user.id)

        return JsonResponse({"emailVerification": _serialize_email_verification(request.user)})

    def delete(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse({"emailVerification": cancel_email_change(request.user)})


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
            "pendingPhone": serialize_phone(get_pending_phone(request.user)),
            "preferredContactMethod": "sms",
        })


class AgentSmsDisableAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        preferred_endpoint = agent.preferred_contact_endpoint

        if preferred_endpoint and preferred_endpoint.channel == CommsChannel.SMS:
            address_belongs_to_user = UserPhoneNumber.objects.filter(
                user=request.user,
                phone_number__iexact=preferred_endpoint.address,
            ).exists()
            if address_belongs_to_user or preferred_endpoint.owner_agent_id is None:
                agent.preferred_contact_endpoint = None
                agent.save(update_fields=["preferred_contact_endpoint"])

        agent_sms_endpoint = agent.comms_endpoints.filter(channel=CommsChannel.SMS).first()
        return JsonResponse({
            "agentSms": {"number": agent_sms_endpoint.address} if agent_sms_endpoint else None,
            "userPhone": serialize_phone(get_primary_phone(request.user)),
            "pendingPhone": serialize_phone(get_pending_phone(request.user)),
            "preferredContactMethod": None,
        })


def _serialize_agent_template_share_state(request: HttpRequest, agent: PersistentAgent) -> dict[str, Any]:
    can_share = agent.organization_id is None
    public_profile = PublicProfile.objects.filter(user=request.user).first()
    suggested_handle = None if public_profile or not can_share else generate_handle_suggestion()
    template = None
    template_url = None
    if public_profile:
        template = (
            PersistentAgentTemplate.objects
            .filter(public_profile=public_profile, source_agent=agent, organization__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if template and template.slug:
            template_url = request.build_absolute_uri(public_template_detail_path(template))

    return {
        "agentId": str(agent.id),
        "agentName": agent.name or "",
        "canShare": can_share,
        "disabledReason": None if can_share else "Organization agents cannot be shared as public templates.",
        "publicProfileHandle": public_profile.handle if public_profile else None,
        "suggestedHandle": suggested_handle,
        "templateUrl": template_url,
        "templateSlug": template.slug if template else None,
        "displayName": template.display_name if template else None,
    }


class AgentTemplateCloneAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id)
        return JsonResponse(_serialize_agent_template_share_state(request, agent))

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
        if not template.slug or not result.public_profile or not result.public_profile.handle:
            return JsonResponse({"error": "Template URL could not be generated."}, status=500)

        if result.created:
            transaction.on_commit(
                lambda: emit_configured_custom_capi_event(
                    user=request.user,
                    event_name=ConfiguredCustomEvent.CLONE_GOBII,
                    plan_owner=agent.organization or request.user,
                    properties={
                        "agent_id": str(agent.id),
                        "template_id": str(template.id),
                        "template_code": template.code,
                    },
                    request=request,
                )
            )
        return JsonResponse({
            "created": result.created,
            **_serialize_agent_template_share_state(request, agent),
        })


@method_decorator(csrf_exempt, name="dispatch")
class AgentTimelineAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def _resume_signup_preview_if_eligible(
        self,
        request: HttpRequest,
        agent: PersistentAgent,
    ) -> PersistentAgent:
        resume_signup_preview_agent_if_eligible(
            agent,
            request.user,
            resume_source="timeline",
        )
        return agent

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        direction_raw = (request.GET.get("direction") or "initial").lower()
        direction: TimelineDirection
        if direction_raw not in {"initial", "older", "newer"}:
            return HttpResponseBadRequest("Invalid direction parameter")
        direction = direction_raw  # type: ignore[assignment]
        developer_mode = str(request.GET.get("developer") or "").strip().lower() in {"1", "true", "yes", "on"}
        if developer_mode:
            if not (request.user.is_staff or request.user.is_superuser):
                return JsonResponse({"error": "forbidden"}, status=403)
            staff_override = get_staff_context_override(request)
            try:
                agent = resolve_staff_agent(request.user, agent_id, staff_override)
            except PermissionDenied:
                raise Http404("Agent not found.")
        else:
            agent = resolve_agent_for_request(
                request,
                agent_id,
                allow_shared=True,
                allow_delinquent_personal_chat=True,
            )
            agent = self._resume_signup_preview_if_eligible(request, agent)

        cursor = request.GET.get("cursor") or None
        try:
            limit = int(request.GET.get("limit", DEFAULT_PAGE_SIZE))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")

        if developer_mode:
            window = fetch_developer_timeline_window(
                agent,
                cursor=cursor,
                direction=direction,
                limit=limit,
            )
        else:
            window = fetch_timeline_window(
                agent,
                cursor=cursor,
                direction=direction,
                limit=limit,
                viewer_user=request.user,
            )
        payload = {
            "events": window.events,
            "has_more_older": window.has_more_older,
            "has_more_newer": window.has_more_newer,
            "processing_active": window.processing_active,
            "processing_snapshot": serialize_processing_snapshot(window.processing_snapshot),
            "current_plan": window.current_plan,
            "agent_name": agent.name,
            "agent_avatar_url": agent.get_avatar_thumbnail_url(),
            "signup_preview_state": agent.signup_preview_state,
            "planning_state": agent.planning_state,
            **serialize_agent_schedule(agent),
            **_pending_action_payload(agent, request.user),
        }
        if direction == "initial":
            payload["critical_status"] = _build_agent_critical_status_payload(request, agent)
        return JsonResponse(payload)

@method_decorator(csrf_exempt, name="dispatch")
class AgentPlanningSkipAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        was_planning = agent.planning_state == PersistentAgent.PlanningState.PLANNING
        agent, _cancelled_count = skip_agent_planning(agent)

        if was_planning:
            inbound_generation = bump_human_inbound_generation(agent.id)
            transaction.on_commit(
                lambda: process_agent_events_task.delay(
                    str(agent.id),
                    inbound_generation=inbound_generation,
                )
            )
            from console.agent_chat.signals import emit_agent_planning_state_update

            emit_agent_planning_state_update(agent, include_pending_actions=True)

        return JsonResponse(
            {
                "planning_state": agent.planning_state,
                **_pending_action_payload(agent, request.user),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentHumanInputRequestResponseAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, request_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        human_input_request = get_object_or_404(
            PersistentAgentHumanInputRequest.objects.select_related(
                "agent",
                "conversation",
                "requested_message__from_endpoint",
            ),
            id=request_id,
            agent=agent,
        )

        if human_input_request.status != PersistentAgentHumanInputRequest.Status.PENDING:
            return JsonResponse({"error": "This request is no longer pending."}, status=400)

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        selected_option_key = str(body.get("selected_option_key") or "").strip() or None
        free_text = str(body.get("free_text") or "").strip() or None
        if bool(selected_option_key) == bool(free_text):
            return JsonResponse(
                {"error": "Provide exactly one of selected_option_key or free_text."},
                status=400,
            )

        try:
            _message = submit_human_input_response(
                human_input_request,
                selected_option_key=selected_option_key,
                free_text=free_text,
                actor_user_id=request.user.id,
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        human_input_request.refresh_from_db()
        action_event = record_human_input_answered(
            agent=agent,
            actor_user=request.user,
            request_ids=[str(human_input_request.id)],
            responses=[human_input_request],
        )

        return JsonResponse(
            {
                **_user_action_event_payload(action_event, request.user),
                **_pending_action_payload(agent, request.user),
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentHumanInputRequestDismissAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, request_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        human_input_request = get_object_or_404(
            PersistentAgentHumanInputRequest.objects.select_related(
                "agent",
                "conversation",
                "requested_message__from_endpoint",
            ),
            id=request_id,
            agent=agent,
        )

        if human_input_request.status != PersistentAgentHumanInputRequest.Status.PENDING:
            return JsonResponse({"error": "This request is no longer pending."}, status=400)

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")
        if not isinstance(body, dict):
            return HttpResponseBadRequest("Invalid JSON body")

        continue_without_answer_value = body.get("continue_without_answer", False)
        if continue_without_answer_value is not False and continue_without_answer_value is not True:
            return JsonResponse(
                {"error": "continue_without_answer must be a boolean."},
                status=400,
            )

        try:
            message = dismiss_human_input_request(
                human_input_request,
                actor_user_id=request.user.id,
                continue_without_answer=continue_without_answer_value,
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        action_event = record_human_input_dismissed(
            agent=agent,
            actor_user=request.user,
            request_id=str(human_input_request.id),
        )
        payload = _pending_action_payload(agent, request.user)
        return JsonResponse(
            {
                **_user_action_event_payload(action_event, request.user),
                **payload,
            },
            status=201 if message else 200,
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentHumanInputRequestBatchResponseAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        responses = body.get("responses")
        if not isinstance(responses, list) or not responses:
            return JsonResponse({"error": "Provide a non-empty responses array."}, status=400)

        normalized_responses: list[dict[str, str]] = []
        for response in responses:
            if not isinstance(response, dict):
                return JsonResponse({"error": "Each batch response must be an object."}, status=400)
            request_id = str(response.get("request_id") or "").strip()
            selected_option_key = str(response.get("selected_option_key") or "").strip()
            free_text = str(response.get("free_text") or "").strip()
            if not request_id:
                return JsonResponse({"error": "Each batch response must include request_id."}, status=400)
            if bool(selected_option_key) == bool(free_text):
                return JsonResponse(
                    {"error": "Each batch response must include exactly one of selected_option_key or free_text."},
                    status=400,
                )
            normalized_responses.append(
                {
                    "request_id": request_id,
                    "selected_option_key": selected_option_key,
                    "free_text": free_text,
                }
            )

        try:
            _message = submit_human_input_responses_batch(
                agent,
                normalized_responses,
                actor_user_id=request.user.id,
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        request_ids = [response["request_id"] for response in normalized_responses]
        answered_requests = list(
            PersistentAgentHumanInputRequest.objects.filter(
                agent=agent,
                id__in=request_ids,
            )
        )
        answered_requests_by_id = {str(request_obj.id): request_obj for request_obj in answered_requests}
        action_event = record_human_input_answered(
            agent=agent,
            actor_user=request.user,
            request_ids=request_ids,
            responses=[
                answered_requests_by_id[request_id]
                for request_id in request_ids
                if request_id in answered_requests_by_id
            ],
        )

        return JsonResponse(
            {
                **_user_action_event_payload(action_event, request.user),
                **_pending_action_payload(agent, request.user),
            },
            status=201,
        )


class AgentSpawnRequestDecisionAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, spawn_request_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        try:
            response_payload = SpawnRequestService.get_request_status(
                agent=agent,
                spawn_request_id=str(spawn_request_id),
            )
        except SpawnRequestResolutionError as exc:
            payload = {"error": str(exc)}
            if exc.request_status:
                payload["request_status"] = exc.request_status
            return JsonResponse(payload, status=exc.status_code)

        return JsonResponse(
            {
                **response_payload,
                **_pending_action_payload(agent, request.user),
            }
        )

    def post(self, request: HttpRequest, agent_id: str, spawn_request_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        if not _can_user_resolve_spawn_requests(
            request.user,
            agent,
            allow_delinquent_personal_chat=True,
        ):
            return JsonResponse({"error": "Not permitted to approve or decline spawn requests."}, status=403)

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        decision = str(body.get("decision") or "").strip().lower()
        try:
            response_payload = SpawnRequestService.resolve_request(
                agent=agent,
                spawn_request_id=str(spawn_request_id),
                decision=decision,
                actor=request.user,
            )
        except SpawnRequestResolutionError as exc:
            payload = {"error": str(exc)}
            if exc.request_status:
                payload["request_status"] = exc.request_status
            return JsonResponse(payload, status=exc.status_code)
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            return JsonResponse({"error": message_text}, status=400)

        def _enqueue_parent_processing() -> None:
            process_agent_events_task.delay(str(agent.pk))

        transaction.on_commit(_enqueue_parent_processing, robust=True)
        return JsonResponse(
            {
                **response_payload,
                **_pending_action_payload(agent, request.user),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentRequestedSecretsFulfillAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        raw_values = body.get("values")
        should_make_global = bool(body.get("make_global"))
        if not isinstance(raw_values, dict):
            return JsonResponse({"error": "values must be an object keyed by secret id."}, status=400)

        normalized_values: dict[str, str] = {}
        validation_errors: dict[str, list[str]] = {}
        for secret_id, value in raw_values.items():
            normalized_secret_id = str(secret_id or "").strip()
            normalized_value = str(value or "")
            if not normalized_secret_id or not normalized_value:
                continue
            try:
                DomainPatternValidator._validate_secret_value(normalized_value)
            except ValueError as exc:
                validation_errors[normalized_secret_id] = [str(exc)]
                continue
            normalized_values[normalized_secret_id] = normalized_value

        if validation_errors:
            return JsonResponse({"errors": validation_errors}, status=400)
        if not normalized_values:
            return JsonResponse({"error": "Provide at least one requested secret value."}, status=400)

        requested_secrets = list(
            PersistentAgentSecret.objects.filter(
                agent=agent,
                requested=True,
                id__in=list(normalized_values.keys()),
            ).order_by("secret_type", "domain_pattern", "name")
        )
        if len(requested_secrets) != len(normalized_values):
            return JsonResponse({"error": "One or more requested secrets could not be found."}, status=404)

        if should_make_global:
            try:
                ensure_global_secret_capacity_for_agent(agent, additional_count=len(requested_secrets))
            except ValidationError as exc:
                return JsonResponse({"errors": {"make_global": [_format_validation_error(exc)]}}, status=400)

            global_errors: dict[str, list[str]] = {}
            for secret in requested_secrets:
                secret.set_value(normalized_values[str(secret.id)])
                try:
                    validate_agent_secret_globalization(secret)
                except ValidationError as exc:
                    global_errors[str(secret.id)] = [_format_validation_error(exc)]
            if global_errors:
                return JsonResponse({"errors": global_errors}, status=400)

        secret_labels = [secret.name for secret in requested_secrets]
        action_event = None
        try:
            with transaction.atomic():
                updated_count = 0
                provided_secret_types: set[str] = set()
                for secret in requested_secrets:
                    secret.set_value(normalized_values[str(secret.id)])
                    if should_make_global:
                        move_agent_secret_to_global(secret)
                    else:
                        secret.requested = False
                        secret.save(update_fields=["encrypted_value", "requested", "updated_at"])
                    updated_count += 1
                    provided_secret_types.add(secret.secret_type)

                if updated_count > 0:
                    step = PersistentAgentStep.objects.create(
                        agent=agent,
                        description=f"User provided {updated_count} requested credential(s)",
                    )
                    PersistentAgentSystemStep.objects.create(
                        step=step,
                        code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
                        notes=f"Secrets provided: {updated_count}",
                    )
                    transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.pk)))
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_SECRETS_PROVIDED,
                        source=AnalyticsSource.WEB,
                        properties={
                            "agent_id": str(agent.pk),
                            "agent_name": agent.name,
                            "secrets_provided": updated_count,
                            "secret_types": sorted(provided_secret_types),
                        },
                    )
                    action_event = record_requested_secrets_saved(
                        agent=agent,
                        actor_user=request.user,
                        secret_labels=secret_labels,
                        make_global=should_make_global,
                    )
        except ValidationError as exc:
            return JsonResponse({"errors": {"__all__": [_format_validation_error(exc)]}}, status=400)
        except IntegrityError:
            return JsonResponse(
                {"errors": {"__all__": ["A secret with that name already exists in this scope."]}},
                status=400,
            )

        return JsonResponse(
            {
                "message": f"Saved {len(requested_secrets)} requested secret value(s).",
                "saved_count": len(requested_secrets),
                **_user_action_event_payload(action_event, request.user),
                **_pending_action_payload(agent, request.user),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentRequestedSecretsRemoveAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        raw_secret_ids = body.get("secret_ids")
        if not isinstance(raw_secret_ids, list) or not raw_secret_ids:
            return JsonResponse({"error": "Provide a non-empty secret_ids array."}, status=400)

        secret_ids = [str(secret_id or "").strip() for secret_id in raw_secret_ids if str(secret_id or "").strip()]
        if not secret_ids:
            return JsonResponse({"error": "Provide a non-empty secret_ids array."}, status=400)

        action_event = None
        with transaction.atomic():
            requested_secrets = list(PersistentAgentSecret.objects.filter(
                agent=agent,
                requested=True,
                id__in=secret_ids,
            ))
            removed_count = len(requested_secrets)
            secret_labels = [secret.name for secret in requested_secrets]
            PersistentAgentSecret.objects.filter(id__in=[secret.id for secret in requested_secrets]).delete()
            if removed_count:
                action_event = record_requested_secrets_removed(
                    agent=agent,
                    actor_user=request.user,
                    secret_labels=secret_labels,
                )

        return JsonResponse(
            {
                "message": f"Removed {removed_count} requested secret(s).",
                "removed_count": removed_count,
                **_user_action_event_payload(action_event, request.user),
                **_pending_action_payload(agent, request.user),
            }
        )


class AgentContactRequestListAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        expire_pending_action_requests(agent)
        pending_requests = list(
            CommsAllowlistRequest.objects.filter(
                agent=agent,
                status=CommsAllowlistRequest.RequestStatus.PENDING,
            ).order_by("-requested_at")
        )
        return JsonResponse(
            {
                "requests": [serialize_contact_request(request_obj) for request_obj in pending_requests],
                "count": len(pending_requests),
                "resolveApiUrl": reverse(
                    "console_agent_contact_requests_resolve",
                    kwargs={"agent_id": agent.id},
                ),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentContactRequestResolveAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )

        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        responses = body.get("responses")
        if not isinstance(responses, list) or not responses:
            return JsonResponse({"error": "Provide a non-empty responses array."}, status=400)

        normalized_responses: list[dict[str, Any]] = []
        for response in responses:
            if not isinstance(response, dict):
                return JsonResponse({"error": "Each response must be an object."}, status=400)
            request_id = str(response.get("request_id") or "").strip()
            decision = str(response.get("decision") or "").strip().lower()
            if not request_id:
                return JsonResponse({"error": "Each response must include request_id."}, status=400)
            if decision not in {"approve", "decline"}:
                return JsonResponse({"error": "decision must be 'approve' or 'decline'."}, status=400)
            can_configure = response.get("can_configure")
            normalized_responses.append(
                {
                    "request_id": request_id,
                    "decision": decision,
                    "allow_inbound": bool(response.get("allow_inbound", True)),
                    "allow_outbound": bool(response.get("allow_outbound", True)),
                    "can_configure": None if can_configure is None else bool(can_configure),
                    "sms_contact_purpose": (
                        str(response.get("sms_contact_purpose") or "").strip() or None
                    ),
                    "sms_contact_purpose_details": (
                        str(response.get("sms_contact_purpose_details") or "").strip() or None
                    ),
                    "sms_contact_permission_attested": bool(
                        response.get("sms_contact_permission_attested", False)
                    ),
                }
            )

        try:
            with transaction.atomic():
                request_ids = [response["request_id"] for response in normalized_responses]
                request_objects = list(
                    CommsAllowlistRequest.objects.select_for_update().filter(
                        agent=agent,
                        id__in=request_ids,
                    )
                )
                requests_by_id = {str(request_obj.id): request_obj for request_obj in request_objects}
                if len(requests_by_id) != len(request_ids):
                    return JsonResponse({"error": "One or more contact requests could not be found."}, status=404)

                response_errors: dict[str, list[str]] = {}
                for response in normalized_responses:
                    request_obj = requests_by_id[response["request_id"]]
                    if request_obj.status != CommsAllowlistRequest.RequestStatus.PENDING:
                        response_errors[response["request_id"]] = ["This request is no longer pending."]
                    elif request_obj.is_expired():
                        response_errors[response["request_id"]] = ["This request has expired."]
                if response_errors:
                    return JsonResponse({"errors": response_errors}, status=400)

                approved_count = 0
                rejected_count = 0
                skipped_count = 0
                approved_addresses: list[str] = []
                resolved_contact_labels: list[str] = []
                approval_capacity: int | None = None
                action_event = None
                now = timezone.now()
                if any(response["decision"] == "approve" for response in normalized_responses):
                    cap = get_user_max_contacts_per_agent(
                        agent.user,
                        organization=agent.organization,
                    )
                    counts = get_agent_contact_counts(agent)
                    if cap > 0 and counts is not None:
                        approval_capacity = max(cap - counts["total"], 0)

                approval_request_objects = [
                    requests_by_id[response["request_id"]]
                    for response in normalized_responses
                    if response["decision"] == "approve"
                ]
                existing_entries_by_key = {
                    (entry.channel, entry.address): entry
                    for entry in CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        is_active=True,
                        channel__in={request_obj.channel for request_obj in approval_request_objects},
                        address__in={request_obj.address for request_obj in approval_request_objects},
                    )
                }
                new_entries: list[CommsAllowlistEntry] = []
                existing_entries_to_update: dict[Any, CommsAllowlistEntry] = {}
                requests_to_update: list[CommsAllowlistRequest] = []
                approved_sms_events: list[tuple[CommsAllowlistRequest, CommsAllowlistEntry]] = []
                sms_purpose_required = sms_contact_purpose_required()

                for response in normalized_responses:
                    request_obj = requests_by_id[response["request_id"]]
                    if response["decision"] == "approve":
                        existing_entry = existing_entries_by_key.get((request_obj.channel, request_obj.address))
                        if (
                            existing_entry is None
                            and approval_capacity is not None
                            and approval_capacity <= 0
                        ):
                            skipped_count += 1
                            continue
                        request_obj.request_inbound = response["allow_inbound"]
                        request_obj.request_outbound = response["allow_outbound"]
                        request_obj.request_configure = (
                            request_obj.request_configure
                            if response["can_configure"] is None
                            else response["can_configure"]
                        )
                        if request_obj.channel == CommsChannel.SMS:
                            if existing_entry is None and agent.organization_id is not None:
                                return JsonResponse(
                                    {
                                        "errors": {
                                            response["request_id"]: [
                                                (
                                                    "Organization agents only support email addresses in allowlists. "
                                                    "Group SMS functionality is not yet available."
                                                )
                                            ]
                                        }
                                    },
                                    status=400,
                                )
                            if response["sms_contact_purpose"] is not None:
                                request_obj.sms_contact_purpose = response["sms_contact_purpose"]
                            if response["sms_contact_purpose_details"] is not None:
                                request_obj.sms_contact_purpose_details = response["sms_contact_purpose_details"]
                            request_obj.sms_contact_permission_attested = response[
                                "sms_contact_permission_attested"
                            ]
                            if request_obj.sms_contact_permission_attested is True:
                                request_obj.sms_contact_permission_attested_at = (
                                    request_obj.sms_contact_permission_attested_at or now
                                )
                            else:
                                request_obj.sms_contact_permission_attested_at = None
                            if (
                                request_obj.sms_contact_permission_attested is True
                                and not request_obj.sms_contact_purpose
                            ):
                                request_obj.sms_contact_purpose = SmsContactPurpose.OTHER_OPERATIONAL
                                request_obj.sms_contact_purpose_details = (
                                    request_obj.sms_contact_purpose_details
                                    or request_obj.purpose
                                    or request_obj.reason
                                    or "Approved through a contact request."
                                )
                            if (
                                existing_entry is None
                                and sms_purpose_required
                                and (
                                    not request_obj.sms_contact_purpose
                                    or request_obj.sms_contact_permission_attested is not True
                                )
                            ):
                                return JsonResponse(
                                    {
                                        "errors": {
                                            response["request_id"]: [
                                                "Confirm you have permission to contact this number by SMS."
                                            ]
                                        }
                                    },
                                    status=400,
                                )
                        else:
                            request_obj.sms_contact_purpose = None
                            request_obj.sms_contact_purpose_details = None
                            request_obj.sms_contact_permission_attested = None
                            request_obj.sms_contact_permission_attested_at = None

                        if existing_entry is None:
                            result = CommsAllowlistEntry(
                                agent=agent,
                                channel=request_obj.channel,
                                address=request_obj.address,
                                is_active=True,
                                allow_inbound=request_obj.request_inbound,
                                allow_outbound=request_obj.request_outbound,
                                can_configure=request_obj.request_configure,
                                sms_contact_purpose=request_obj.sms_contact_purpose,
                                sms_contact_purpose_details=request_obj.sms_contact_purpose_details,
                                sms_contact_permission_attested=request_obj.sms_contact_permission_attested,
                                sms_contact_permission_attested_at=request_obj.sms_contact_permission_attested_at,
                                created_at=now,
                                updated_at=now,
                            )
                            new_entries.append(result)
                            existing_entries_by_key[(result.channel, result.address)] = result
                        else:
                            result = existing_entry
                            if request_obj.channel == CommsChannel.SMS and (
                                request_obj.sms_contact_purpose
                                or request_obj.sms_contact_permission_attested is True
                            ):
                                existing_entry.sms_contact_purpose = request_obj.sms_contact_purpose
                                existing_entry.sms_contact_purpose_details = (
                                    request_obj.sms_contact_purpose_details
                                )
                                existing_entry.sms_contact_permission_attested = (
                                    request_obj.sms_contact_permission_attested
                                )
                                existing_entry.sms_contact_permission_attested_at = (
                                    request_obj.sms_contact_permission_attested_at
                                )
                                existing_entry.updated_at = now
                                if existing_entry.pk is not None:
                                    existing_entries_to_update[existing_entry.pk] = existing_entry

                        request_obj.status = CommsAllowlistRequest.RequestStatus.APPROVED
                        request_obj.responded_at = now
                        requests_to_update.append(request_obj)
                        if existing_entry is None and approval_capacity is not None:
                            approval_capacity -= 1
                        if request_obj.channel == CommsChannel.SMS:
                            approved_sms_events.append((request_obj, result))
                        approved_count += 1
                        contact_label = request_obj.name or request_obj.address
                        approved_addresses.append(contact_label)
                        resolved_contact_labels.append(contact_label)
                    else:
                        request_obj.status = CommsAllowlistRequest.RequestStatus.REJECTED
                        request_obj.responded_at = now
                        requests_to_update.append(request_obj)
                        rejected_count += 1
                        resolved_contact_labels.append(request_obj.name or request_obj.address)

                if new_entries:
                    CommsAllowlistEntry.objects.bulk_create(new_entries, batch_size=500)
                if existing_entries_to_update:
                    CommsAllowlistEntry.objects.bulk_update(
                        list(existing_entries_to_update.values()),
                        [
                            "sms_contact_purpose",
                            "sms_contact_purpose_details",
                            "sms_contact_permission_attested",
                            "sms_contact_permission_attested_at",
                            "updated_at",
                        ],
                        batch_size=500,
                    )
                if requests_to_update:
                    CommsAllowlistRequest.objects.bulk_update(
                        requests_to_update,
                        [
                            "request_inbound",
                            "request_outbound",
                            "request_configure",
                            "sms_contact_purpose",
                            "sms_contact_purpose_details",
                            "sms_contact_permission_attested",
                            "sms_contact_permission_attested_at",
                            "status",
                            "responded_at",
                        ],
                        batch_size=500,
                    )
                    transaction.on_commit(lambda: _emit_pending_action_requests_update_on_commit(agent))

                for sms_request, sms_entry in approved_sms_events:
                    sms_event_kwargs = {
                        "user_id": request.user.id,
                        "agent": agent,
                        "address": sms_request.address,
                        "approval_source": "agent_chat_contact_request_resolve",
                        "approval_action": "approve",
                        "allow_inbound": sms_request.request_inbound,
                        "allow_outbound": sms_request.request_outbound,
                        "can_configure": sms_request.request_configure,
                        "sms_contact_purpose": sms_request.sms_contact_purpose,
                        "sms_contact_purpose_details": sms_request.sms_contact_purpose_details,
                        "sms_contact_permission_attested": (
                            sms_request.sms_contact_permission_attested
                        ),
                        "allowlist_entry_id": getattr(sms_entry, "id", None),
                        "contact_request_id": str(sms_request.id),
                    }
                    transaction.on_commit(
                        lambda kwargs=sms_event_kwargs: track_sms_contact_approval(**kwargs)
                    )

                if approved_count > 0:
                    if agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                        agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                        agent.save(update_fields=["whitelist_policy"])

                    step = PersistentAgentStep.objects.create(
                        agent=agent,
                        description=f"User approved {approved_count} contact request(s)",
                    )
                    PersistentAgentSystemStep.objects.create(
                        step=step,
                        code=PersistentAgentSystemStep.Code.CONTACTS_APPROVED,
                        notes=f"Approved: {', '.join(approved_addresses)}",
                    )
                    transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.pk)))
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.AGENT_CONTACTS_APPROVED,
                        source=AnalyticsSource.WEB,
                        properties={
                            "agent_id": str(agent.pk),
                            "agent_name": agent.name,
                            "approved_count": approved_count,
                            "rejected_count": rejected_count,
                            "invitations_sent": 0,
                        },
                    )
                action_event = record_contact_requests_resolved(
                    agent=agent,
                    actor_user=request.user,
                    approved_count=approved_count,
                    declined_count=rejected_count,
                    skipped_count=skipped_count,
                    contact_labels=resolved_contact_labels,
                )
        except ValidationError as exc:
            return JsonResponse({"errors": {"__all__": [_format_validation_error(exc)]}}, status=400)
        except ValueError as exc:
            return JsonResponse({"errors": {"__all__": [str(exc)]}}, status=400)

        return JsonResponse(
            {
                "message": (
                    f"Approved {approved_count}, declined {rejected_count}, and left {skipped_count} pending due to the contact limit."
                    if skipped_count
                    else f"Approved {approved_count} and declined {rejected_count} contact request(s)."
                    if approved_count or rejected_count
                    else "No contact requests were updated."
                ),
                "approved_count": approved_count,
                "rejected_count": rejected_count,
                "skipped_count": skipped_count,
                **_user_action_event_payload(action_event, request.user),
                **_pending_action_payload(agent, request.user),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentMessageCreateAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        if not user_has_natural_agent_chat_access(request.user, agent):
            return JsonResponse({"error": "This staff view is read-only for user messages."}, status=403)
        if (
            agent.organization_id is None
            and agent.user_id is not None
            and not can_user_send_personal_agent_chat_message(agent.user)
        ):
            return JsonResponse({"error": "Choose a plan to send more messages."}, status=403)
        owner = agent.organization or agent.user
        if owner is not None and bool(get_owner_account_pause_state(owner).get("customer_paused")):
            return JsonResponse({"error": _customer_account_pause_block_message(owner)}, status=403)
        attachments: list[Any] = []
        message_text = ""
        if request.content_type and request.content_type.startswith("multipart/form-data"):
            try:
                message_text = (request.POST.get("body") or "").strip()
                attachments = list(request.FILES.getlist("attachments") or request.FILES.values())
            except (MultiPartParserError, RequestDataTooBig):
                max_size_label = filesizeformat(get_max_file_size() or 0).replace("\xa0", " ")
                return JsonResponse(
                    {"error": f"Upload is too large. Max file size is {max_size_label}."},
                    status=400,
                )
            oversize_error = _validate_console_chat_attachments(attachments)
            if oversize_error is not None:
                return JsonResponse({"error": oversize_error}, status=400)
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
            is_visible=True,
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
        info = ingest_inbound_message(
            CommsChannel.WEB,
            parsed,
            filespace_import_mode="sync",
            prioritize_processing_dispatch=True,
        )
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


AGENT_MESSAGE_REPORT_COMMENT_MAX_LENGTH = 2000


def _resolve_actionable_agent_message(agent: PersistentAgent, message_id: str) -> PersistentAgentMessage:
    message = (
        PersistentAgentMessage.objects.filter(
            id=message_id,
            owner_agent=agent,
            is_outbound=True,
            peer_agent__isnull=True,
        )
        .select_related("conversation", "from_endpoint", "owner_agent")
        .first()
    )
    if message is None:
        raise Http404("Message not found.")
    return message


def _resolve_agent_message_action(
    request: HttpRequest,
    agent_id: str,
    message_id: str,
) -> tuple[PersistentAgent, PersistentAgentMessage]:
    agent = resolve_agent_for_request(
        request,
        agent_id,
        allow_shared=True,
        allow_delinquent_personal_chat=True,
    )
    return agent, _resolve_actionable_agent_message(agent, message_id)


def _agent_message_action_properties(
    agent: PersistentAgent,
    message: PersistentAgentMessage,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "message_id": str(message.id),
        "message_channel": (
            message.conversation.channel
            if message.conversation_id
            else message.from_endpoint.channel if message.from_endpoint_id else ""
        ),
        "message_timestamp": message.timestamp.isoformat() if message.timestamp else "",
    }
    if extra:
        properties.update(extra)
    return _web_chat_properties(agent, properties)


@method_decorator(csrf_exempt, name="dispatch")
class AgentMessageCopyAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, message_id: str, *args: Any, **kwargs: Any):
        agent, message = _resolve_agent_message_action(request, agent_id, message_id)
        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.AGENT_MESSAGE_COPIED,
            source=AnalyticsSource.WEB,
            properties=_agent_message_action_properties(agent, message),
        )
        return JsonResponse({"ok": True})


class AgentMessageFeedbackAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, message_id: str, *args: Any, **kwargs: Any):
        agent, message = _resolve_agent_message_action(request, agent_id, message_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        if "feedback" not in payload:
            return HttpResponseBadRequest("feedback is required")
        feedback = payload["feedback"]
        if feedback is not None and feedback not in PersistentAgentMessageFeedback.Rating.values:
            return HttpResponseBadRequest("feedback must be 'up', 'down', or null")

        with transaction.atomic():
            existing = (
                PersistentAgentMessageFeedback.objects.select_for_update()
                .filter(message=message, user=request.user)
                .first()
            )
            previous_feedback = existing.rating if existing else None
            changed = previous_feedback != feedback
            if changed and feedback is None:
                existing.delete()
            elif changed and existing is not None:
                existing.rating = feedback
                existing.save(update_fields=["rating", "updated_at"])
            elif changed:
                PersistentAgentMessageFeedback.objects.update_or_create(
                    message=message,
                    user=request.user,
                    defaults={"rating": feedback},
                )

        if changed:
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_MESSAGE_FEEDBACK_UPDATED,
                source=AnalyticsSource.WEB,
                properties=_agent_message_action_properties(
                    agent,
                    message,
                    {
                        "previous_feedback": previous_feedback or "none",
                        "feedback": feedback or "none",
                    },
                ),
            )

        return JsonResponse({"ok": True, "feedback": feedback})


@method_decorator(csrf_exempt, name="dispatch")
class AgentMessageReportIssueAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, message_id: str, *args: Any, **kwargs: Any):
        agent, message = _resolve_agent_message_action(request, agent_id, message_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        raw_comment = payload.get("comment", "")
        if raw_comment is None:
            raw_comment = ""
        if not isinstance(raw_comment, str):
            return HttpResponseBadRequest("comment must be a string")
        comment = raw_comment.strip()[:AGENT_MESSAGE_REPORT_COMMENT_MAX_LENGTH]

        try:
            send_agent_message_report_email(user=request.user, agent=agent, message=message, comment=comment)
        except SupportRequestConfigurationError as exc:
            logger.warning(
                "Message report email was not sent for agent %s message %s: %s",
                agent.id,
                message.id,
                exc,
            )
        except (AnymailAPIError, BadHeaderError, OSError, SMTPException):
            logger.exception("Failed to email message report for agent %s message %s.", agent.id, message.id)
        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.AGENT_MESSAGE_ISSUE_REPORTED,
            source=AnalyticsSource.WEB,
            properties=_agent_message_action_properties(
                agent,
                message,
                {
                    "comment_length": len(comment),
                    "comment_provided": bool(comment),
                    "comment_truncated": len(raw_comment.strip()) > AGENT_MESSAGE_REPORT_COMMENT_MAX_LENGTH,
                },
            ),
        )

        transaction.on_commit(lambda: run_reported_agent_judge_task.delay(str(agent.id), str(message.id), comment))
        return JsonResponse({"ok": True, "judge": {"ran": False, "status": "queued"}}, status=202)


@method_decorator(csrf_exempt, name="dispatch")
class AgentLatestMessageReadAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        mark_latest_visible_outbound_message_read(agent, request.user, READ_SOURCE_CHAT_OPEN)
        return JsonResponse(serialize_agent_message_read_state(agent, request.user))


def _validate_console_chat_attachments(attachments: list[Any]) -> str | None:
    max_bytes = get_max_file_size()
    if not max_bytes:
        return None

    for attachment in attachments:
        size = getattr(attachment, "size", None)
        try:
            size_bytes = int(size)
        except (TypeError, ValueError):
            continue

        if size_bytes > int(max_bytes):
            filename = getattr(attachment, "name", None) or "attachment"
            max_size_label = filesizeformat(int(max_bytes)).replace("\xa0", " ")
            return f'"{filename}" is too large. Max file size is {max_size_label}.'

    return None


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
        agent = get_object_or_404(
            PersistentAgent.objects.alive().select_related("organization"),
            pk=agent_id,
        )
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
                    AgentFsNode.objects.alive()
                    .filter(
                        id=node_id,
                        filespace_id__in=filespace_ids,
                        node_type=AgentFsNode.NodeType.FILE,
                    )
                    .first()
                )
            else:
                matches = AgentFsNode.objects.alive().filter(
                    filespace_id__in=filespace_ids,
                    path=path,
                    node_type=AgentFsNode.NodeType.FILE,
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
            AgentFsNode.objects.alive()
            .filter(
                id=node_uuid,
                filespace_id__in=filespace_ids,
                node_type=AgentFsNode.NodeType.FILE,
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
        response = _build_filespace_download_response(node)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


def _serialize_agent_fs_node(node: AgentFsNode) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "parentId": str(node.parent_id) if node.parent_id else None,
        "name": node.name,
        "path": node.path,
        "nodeType": node.node_type,
        "sizeBytes": node.size_bytes,
        "updatedAt": node.updated_at.isoformat() if node.updated_at else None,
    }


class AgentFsNodeListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(request, agent_id, allow_shared=True)
        filespace_access = (
            AgentFileSpaceAccess.objects
            .filter(agent=agent)
            .order_by("-is_default", "-granted_at")
            .first()
        )
        if filespace_access is None:
            nodes = []
            filespace_id = None
        else:
            filespace_id = filespace_access.filespace_id
            nodes = list(
                AgentFsNode.objects.alive()
                .filter(filespace_id=filespace_id)
                .only(
                    "id",
                    "parent_id",
                    "name",
                    "path",
                    "node_type",
                    "size_bytes",
                    "updated_at",
                )
                .order_by("parent_id", "node_type", "name")
            )

        try:
            node_count = len(nodes)
            file_count = sum(1 for node in nodes if node.node_type == AgentFsNode.NodeType.FILE)
            dir_count = node_count - file_count
            Analytics.track_event(
                user_id=str(request.user.id),
                event=AnalyticsEvent.AGENT_FILES_VIEWED,
                source=AnalyticsSource.WEB,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "filespace_id": str(filespace_id) if filespace_id else None,
                        "node_count": node_count,
                        "file_count": file_count,
                        "dir_count": dir_count,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logger.debug("Failed to emit agent files viewed analytics for agent %s", agent.id, exc_info=True)

        payload = {"nodes": [_serialize_agent_fs_node(node) for node in nodes]}
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
                AgentFsNode.objects.alive()
                .filter(
                    filespace=filespace,
                    id=parent_id,
                    node_type=AgentFsNode.NodeType.DIR,
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
                AgentFsNode.objects.alive()
                .filter(
                    filespace=filespace,
                    path=parent_path,
                    node_type=AgentFsNode.NodeType.DIR,
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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        node_ids = payload.get("node_ids") or payload.get("nodeIds") or []
        if not isinstance(node_ids, list) or not node_ids:
            return HttpResponseBadRequest("node_ids must be a non-empty list")

        filespace = get_or_create_default_filespace(agent)
        nodes = list(
            AgentFsNode.objects.alive()
            .filter(
                filespace=filespace,
                id__in=node_ids,
            )
            .order_by("path")
        )

        deleted = 0
        deleted_prefixes: list[str] = []
        for node in sorted(nodes, key=lambda item: item.path.count("/")):
            if any(node.path == prefix or node.path.startswith(f"{prefix}/") for prefix in deleted_prefixes):
                continue
            deleted += node.trash_subtree()
            if node.node_type == AgentFsNode.NodeType.DIR:
                deleted_prefixes.append(node.path.rstrip("/"))

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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        name = str(payload.get("name") or "").strip()
        if not name:
            return HttpResponseBadRequest("name is required")

        parent_id = (payload.get("parent_id") or payload.get("parentId") or "").strip()
        filespace = get_or_create_default_filespace(agent)
        parent = None
        if parent_id:
            parent = (
                AgentFsNode.objects.alive()
                .filter(
                    filespace=filespace,
                    id=parent_id,
                    node_type=AgentFsNode.NodeType.DIR,
                )
                .first()
            )
            if not parent:
                return HttpResponseBadRequest("parent_id is invalid")

        if AgentFsNode.objects.alive().filter(filespace=filespace, parent=parent, name=name).exists():
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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
            AgentFsNode.objects.alive()
            .filter(filespace=filespace, id=node_id)
            .first()
        )
        if not node:
            return HttpResponseBadRequest("node_id is invalid")

        old_parent = node.parent
        old_parent_path = old_parent.path if old_parent else "/"
        parent = None
        if parent_id:
            parent = (
                AgentFsNode.objects.alive()
                .filter(
                    filespace=filespace,
                    id=parent_id,
                    node_type=AgentFsNode.NodeType.DIR,
                )
                .first()
            )
            if not parent:
                return HttpResponseBadRequest("parent_id is invalid")

        if node.parent_id == (parent.id if parent else None):
            return JsonResponse({"node": _serialize_agent_fs_node(node)})

        name_conflict = (
            AgentFsNode.objects.alive()
            .filter(filespace=filespace, parent=parent, name=node.name)
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


class SystemSettingsListAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse({"settings": list_system_settings()})


class SystemStatusAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse(build_system_status_payload())


class SystemSettingDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, key: str, *args: Any, **kwargs: Any):
        definition = get_setting_definition(key)
        if definition is None:
            return HttpResponseBadRequest("Unknown system setting")

        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        if _coerce_bool(payload.get("clear")):
            try:
                clear_setting_value(definition)
            except (ValueError, ValidationError) as exc:
                return HttpResponseBadRequest(str(exc))
            return JsonResponse({"ok": True, "setting": serialize_setting(definition)})

        if "value" not in payload:
            return HttpResponseBadRequest("value is required")

        try:
            coerced = definition.coerce(payload.get("value"))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        try:
            set_setting_value(definition, coerced)
        except (ValueError, ValidationError) as exc:
            return HttpResponseBadRequest(str(exc))
        return JsonResponse({"ok": True, "setting": serialize_setting(definition)})

    def delete(self, request: HttpRequest, key: str, *args: Any, **kwargs: Any):
        definition = get_setting_definition(key)
        if definition is None:
            return HttpResponseBadRequest("Unknown system setting")
        try:
            clear_setting_value(definition)
        except (ValueError, ValidationError) as exc:
            return HttpResponseBadRequest(str(exc))
        return JsonResponse({"ok": True, "setting": serialize_setting(definition)})


class LLMProviderListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        display_name = (payload.get("display_name") or "").strip()
        key = (payload.get("key") or "").strip()
        if not display_name or not key:
            return HttpResponseBadRequest("display_name and key are required")

        if LLMProvider.objects.filter(key=key).exists():
            return HttpResponseBadRequest("Provider key already exists")

        try:
            browser_backend = _coerce_provider_browser_backend(payload.get("browser_backend"))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        provider = LLMProvider(
            display_name=display_name,
            key=key,
            enabled=_coerce_bool(payload.get("enabled", True)),
            env_var_name=(payload.get("env_var_name") or "").strip(),
            model_prefix=(payload.get("model_prefix") or "").strip(),
            browser_backend=browser_backend,
            supports_safety_identifier=_coerce_bool(payload.get("supports_safety_identifier", False)),
            vertex_project=(payload.get("vertex_project") or "").strip(),
            vertex_location=(payload.get("vertex_location") or "").strip(),
        )
        api_key_value = payload.get("api_key")
        if api_key_value:
            provider.api_key_encrypted = SecretsEncryption.encrypt_value(api_key_value)
        try:
            provider.full_clean()
        except ValidationError as exc:
            return HttpResponseBadRequest(_format_validation_error(exc))
        provider.save()
        return _json_ok(provider_id=str(provider.id))


class LLMProviderDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, provider_id: str, *args: Any, **kwargs: Any):
        provider = get_object_or_404(LLMProvider, pk=provider_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
                if model_field == "browser_backend":
                    try:
                        value = _coerce_provider_browser_backend(value)
                    except ValueError as exc:
                        return HttpResponseBadRequest(str(exc))
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
            or provider.image_generation_endpoints.exists()
        )
        if has_dependents:
            return HttpResponseBadRequest("Provider cannot be deleted while endpoints exist")
        provider.delete()
        return _json_ok()


class LLMEndpointTestAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
                    responses_api=True,
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
            elif kind == "image_generation":
                endpoint = get_object_or_404(ImageGenerationModelEndpoint, pk=endpoint_id)
                result = _run_image_generation_test(endpoint)
            elif kind == "video_generation":
                endpoint = get_object_or_404(VideoGenerationModelEndpoint, pk=endpoint_id)
                result = _run_video_generation_test(endpoint)
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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
            litellm_pricing_model=(payload.get("litellm_pricing_model") or "").strip() or None,
            temperature_override=temperature_override,
            supports_temperature=_coerce_bool(payload.get("supports_temperature", True)),
            supports_tool_choice=_coerce_bool(payload.get("supports_tool_choice", True)),
            use_parallel_tool_calls=_coerce_bool(payload.get("use_parallel_tool_calls", True)),
            allow_implied_send=_coerce_bool(payload.get("allow_implied_send", True)),
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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        if "model" in payload or "litellm_model" in payload:
            model = (payload.get("model") or payload.get("litellm_model") or "").strip()
            if model:
                if endpoint.provider and endpoint.provider.model_prefix and model.startswith(endpoint.provider.model_prefix):
                    return HttpResponseBadRequest("Store persistent models without the provider prefix; it is applied at runtime.")
                endpoint.litellm_model = model
        if "litellm_pricing_model" in payload:
            endpoint.litellm_pricing_model = (payload.get("litellm_pricing_model") or "").strip() or None
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
        if "allow_implied_send" in payload:
            endpoint.allow_implied_send = _coerce_bool(payload.get("allow_implied_send"))
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
        force = request.GET.get("force") in {"1", "true", "yes"}
        tier_usage = build_persistent_endpoint_tier_usage(endpoint)
        if tier_usage and not force:
            return JsonResponse(
                {
                    "ok": False,
                    "code": "endpoint_in_tiers",
                    "message": "Endpoint is assigned to persistent routing tiers.",
                    "tier_usage": tier_usage,
                },
                status=409,
            )
        with transaction.atomic():
            PersistentTierEndpoint.objects.filter(endpoint=endpoint).delete()
            ProfilePersistentTierEndpoint.objects.filter(endpoint=endpoint).delete()
            LLMRoutingProfile.objects.filter(eval_judge_endpoint=endpoint).update(eval_judge_endpoint=None)
            LLMRoutingProfile.objects.filter(summarization_endpoint=endpoint).update(summarization_endpoint=None)
            LLMRoutingProfile.objects.filter(agent_judge_endpoint=endpoint).update(agent_judge_endpoint=None)
            endpoint.delete()
        invalidate_llm_bootstrap_cache()
        from api.agent.core.llm_config import invalidate_min_endpoint_input_tokens_cache
        invalidate_min_endpoint_input_tokens_cache()
        return _json_ok()


def _parse_token_range_values(payload, *, current=None, default_min_tokens=None):
    name = (payload.get("name", current.name if current else "") or "").strip()
    if not name:
        return None, HttpResponseBadRequest("name is required")
    try:
        min_tokens = int(payload.get("min_tokens", current.min_tokens if current else default_min_tokens))
    except (TypeError, ValueError):
        return None, HttpResponseBadRequest("min_tokens must be an integer")
    raw_max_tokens = payload.get("max_tokens", current.max_tokens if current else None)
    try:
        max_tokens = None if raw_max_tokens in (None, "") else int(raw_max_tokens)
    except (TypeError, ValueError):
        return None, HttpResponseBadRequest("max_tokens must be an integer or null")
    if max_tokens is not None and max_tokens <= min_tokens:
        return None, HttpResponseBadRequest("max_tokens must be greater than min_tokens")
    return {"name": name, "min_tokens": min_tokens, "max_tokens": max_tokens}, None


class _TokenRangeListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]
    range_model = None
    parent_model = None
    parent_kwarg = ""
    parent_field = ""
    response_id_key = "range_id"
    default_min_tokens = None
    invalidate = staticmethod(lambda: None)

    def _parent(self, kwargs):
        if not self.parent_model:
            return None
        return get_object_or_404(self.parent_model, pk=kwargs[self.parent_kwarg])

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        parent = self._parent(kwargs)
        ranges = self.range_model.objects.filter(**{self.parent_field: parent}).order_by("min_tokens")
        return JsonResponse({
            "ranges": [
                {"id": str(item.id), "name": item.name, "min_tokens": item.min_tokens, "max_tokens": item.max_tokens}
                for item in ranges
            ]
        })

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        parent = self._parent(kwargs)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        values, error = _parse_token_range_values(payload, default_min_tokens=self.default_min_tokens)
        if error:
            return error
        if parent:
            values[self.parent_field] = parent
        token_range = self.range_model.objects.create(**values)
        self.invalidate()
        return _json_ok(**{self.response_id_key: str(token_range.id)})


class _TokenRangeDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]
    range_model = None
    response_id_key = "range_id"
    invalidate = staticmethod(lambda: None)

    def patch(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        token_range = get_object_or_404(self.range_model, pk=range_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        values, error = _parse_token_range_values(payload, current=token_range)
        if error:
            return error
        for field, value in values.items():
            setattr(token_range, field, value)
        token_range.save()
        self.invalidate()
        return _json_ok(**{self.response_id_key: str(token_range.id)})

    def delete(self, request: HttpRequest, range_id: str, *args: Any, **kwargs: Any):
        get_object_or_404(self.range_model, pk=range_id).delete()
        self.invalidate()
        return _json_ok()


class PersistentTokenRangeListCreateAPIView(_TokenRangeListCreateAPIView):
    range_model = PersistentTokenRange
    response_id_key = "token_range_id"
    invalidate = staticmethod(invalidate_llm_bootstrap_cache)


class PersistentTokenRangeDetailAPIView(_TokenRangeDetailAPIView):
    range_model = PersistentTokenRange
    response_id_key = "token_range_id"
    invalidate = staticmethod(invalidate_llm_bootstrap_cache)


class ProfileTokenRangeListCreateAPIView(_TokenRangeListCreateAPIView):
    http_method_names = ["get", "post"]
    range_model = ProfileTokenRange
    parent_model = LLMRoutingProfile
    parent_kwarg = "profile_id"
    parent_field = "profile"
    default_min_tokens = 0


class ProfileTokenRangeDetailAPIView(_TokenRangeDetailAPIView):
    range_model = ProfileTokenRange


class _LLMTierListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]
    tier_model = None
    parent_model = None
    parent_kwarg = ""
    parent_field = ""
    next_order_fn = None
    order_by = ("order",)
    include_intelligence_tier = True
    auto_append_order = False
    invalidate = staticmethod(lambda: None)

    def _get_parent(self, kwargs):
        return get_object_or_404(self.parent_model, pk=kwargs[self.parent_kwarg])

    def _tier_payload(self, tier):
        payload = {"id": str(tier.id), "order": tier.order, "description": tier.description}
        if self.include_intelligence_tier:
            payload["intelligence_tier"] = serialize_intelligence_tier(tier.intelligence_tier)
        return payload

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        parent = self._get_parent(kwargs)
        tiers = self.tier_model.objects.filter(**{self.parent_field: parent}).order_by(*self.order_by)
        return JsonResponse({"tiers": [self._tier_payload(tier) for tier in tiers]})

    def _create_order(self, payload, parent, intelligence_tier):
        if self.next_order_fn:
            return self.next_order_fn(parent, intelligence_tier), None
        try:
            order = int(payload["order"]) if payload.get("order") is not None else None
        except (TypeError, ValueError):
            return None, HttpResponseBadRequest("order must be an integer")
        if self.auto_append_order:
            filters = {self.parent_field: parent, "intelligence_tier": intelligence_tier}
            if order is None or order <= 0 or self.tier_model.objects.filter(**filters, order=order).exists():
                order = _next_profile_tier_order(self.tier_model, **filters)
        return order or 0, None

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        parent = self._get_parent(kwargs)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        create_kwargs = {
            self.parent_field: parent,
            "description": (payload.get("description") or "").strip(),
        }
        intelligence_tier = None
        if self.include_intelligence_tier:
            try:
                intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
            create_kwargs["intelligence_tier"] = intelligence_tier
        order, error = self._create_order(payload, parent, intelligence_tier)
        if error:
            return error
        tier = self.tier_model.objects.create(order=order, **create_kwargs)
        self.invalidate()
        return _json_ok(tier_id=str(tier.id))


class _LLMTierDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]
    tier_model = None
    sibling_filter_fields = ()
    allow_order_update = False
    allow_intelligence_update = False
    invalidate = staticmethod(lambda: None)

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(self.tier_model, pk=tier_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        if "description" in payload:
            tier.description = (payload.get("description") or "").strip()
        if "move" in payload:
            direction = (payload.get("move") or "").lower()
            if direction not in {"up", "down"}:
                return HttpResponseBadRequest("direction must be 'up' or 'down'")
            sibling_qs = self.tier_model.objects.filter(
                **{field: getattr(tier, field) for field in self.sibling_filter_fields}
            )
            if not _swap_orders(sibling_qs, tier, direction):
                return HttpResponseBadRequest("Unable to move tier in that direction")
        if self.allow_order_update and "order" in payload:
            tier.order = payload.get("order", 0)
        if self.allow_intelligence_update and any(
            key in payload for key in ("intelligence_tier", "is_premium", "is_max")
        ):
            try:
                tier.intelligence_tier = _resolve_intelligence_tier_from_payload(payload)
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
        tier.save()
        self.invalidate()
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        get_object_or_404(self.tier_model, pk=tier_id).delete()
        self.invalidate()
        return _json_ok()


class _LLMTierEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]
    tier_model = None
    endpoint_model = None
    tier_endpoint_model = None
    allow_reasoning_override = False
    allow_extraction_endpoint = False
    endpoint_model_attr = "litellm_model"
    require_endpoint_id = False
    reject_duplicates = True
    invalidate = staticmethod(lambda: None)

    def _endpoint_label(self, endpoint) -> str:
        provider = endpoint.provider.display_name if endpoint.provider else "Unlinked"
        return f"{provider} · {getattr(endpoint, self.endpoint_model_attr)}"

    def _payload(self, tier_endpoint):
        payload = {
            "id": str(tier_endpoint.id),
            "endpoint_id": str(tier_endpoint.endpoint_id),
            "label": self._endpoint_label(tier_endpoint.endpoint),
            "weight": float(tier_endpoint.weight),
        }
        if self.allow_reasoning_override:
            payload.update(
                reasoning_effort_override=tier_endpoint.reasoning_effort_override,
                supports_reasoning=tier_endpoint.endpoint.supports_reasoning,
                endpoint_reasoning_effort=tier_endpoint.endpoint.reasoning_effort,
            )
        if self.allow_extraction_endpoint:
            extraction = tier_endpoint.extraction_endpoint
            payload.update(
                extraction_endpoint_id=str(tier_endpoint.extraction_endpoint_id) if extraction else None,
                extraction_label=self._endpoint_label(extraction) if extraction else None,
            )
        return payload

    def get(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(self.tier_model, pk=tier_id)
        select_related = ["endpoint__provider"]
        if self.allow_extraction_endpoint:
            select_related.append("extraction_endpoint__provider")
        endpoints = self.tier_endpoint_model.objects.filter(tier=tier).select_related(*select_related)
        return JsonResponse({"endpoints": [self._payload(endpoint) for endpoint in endpoints]})

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(self.tier_model, pk=tier_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        endpoint_id = payload.get("endpoint_id")
        if self.require_endpoint_id and not endpoint_id:
            return HttpResponseBadRequest("endpoint_id is required")
        endpoint = get_object_or_404(self.endpoint_model, pk=endpoint_id)
        if self.reject_duplicates and tier.tier_endpoints.filter(endpoint=endpoint).exists():
            return HttpResponseBadRequest("Endpoint already exists in tier")
        weight = _parse_weight(payload, default=1)
        if isinstance(weight, HttpResponseBadRequest):
            return weight
        create_kwargs = {"tier": tier, "endpoint": endpoint, "weight": weight}
        if self.allow_reasoning_override:
            try:
                create_kwargs["reasoning_effort_override"] = _validate_reasoning_override(endpoint, payload.get("reasoning_effort_override"))
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
        if self.allow_extraction_endpoint:
            extraction_endpoint_id = payload.get("extraction_endpoint_id")
            create_kwargs["extraction_endpoint"] = (
                get_object_or_404(self.endpoint_model, pk=extraction_endpoint_id)
                if extraction_endpoint_id
                else None
            )
        te = self.tier_endpoint_model.objects.create(**create_kwargs)
        self.invalidate()
        return _json_ok(tier_endpoint_id=str(te.id))


class _LLMTierEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]
    tier_endpoint_model = None
    endpoint_model = None
    allow_reasoning_override = False
    allow_extraction_endpoint = False
    invalidate = staticmethod(lambda: None)

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(self.tier_endpoint_model, pk=tier_endpoint_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        if "weight" in payload:
            weight = _parse_weight(payload, default=None)
            if isinstance(weight, HttpResponseBadRequest):
                return weight
            tier_endpoint.weight = weight
        if self.allow_reasoning_override and "reasoning_effort_override" in payload:
            try:
                tier_endpoint.reasoning_effort_override = _validate_reasoning_override(
                    tier_endpoint.endpoint,
                    payload.get("reasoning_effort_override"),
                )
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
        if self.allow_extraction_endpoint and "extraction_endpoint_id" in payload:
            extraction_endpoint_id = payload.get("extraction_endpoint_id")
            tier_endpoint.extraction_endpoint = (
                get_object_or_404(self.endpoint_model, pk=extraction_endpoint_id)
                if extraction_endpoint_id
                else None
            )
        tier_endpoint.save()
        self.invalidate()
        return _json_ok(tier_endpoint_id=str(tier_endpoint.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        get_object_or_404(self.tier_endpoint_model, pk=tier_endpoint_id).delete()
        self.invalidate()
        return _json_ok()


class BrowserEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
        force = request.GET.get("force") in {"1", "true", "yes"}
        tier_usage = build_browser_endpoint_tier_usage(endpoint)
        if tier_usage and not force:
            return JsonResponse(
                {
                    "ok": False,
                    "code": "endpoint_in_tiers",
                    "message": "Endpoint is assigned to browser tiers.",
                    "tier_usage": tier_usage,
                },
                status=409,
            )
        with transaction.atomic():
            BrowserTierEndpoint.objects.filter(endpoint=endpoint).delete()
            ProfileBrowserTierEndpoint.objects.filter(endpoint=endpoint).delete()
            BrowserTierEndpoint.objects.filter(extraction_endpoint=endpoint).update(extraction_endpoint=None)
            ProfileBrowserTierEndpoint.objects.filter(extraction_endpoint=endpoint).update(extraction_endpoint=None)
            endpoint.delete()
        return _json_ok()


def _active_browser_policy_parent(self, kwargs):
    return _get_active_browser_policy()


for _name, _tier_model, _endpoint_model, _tier_endpoint_model, _list_attrs, _detail_attrs, _endpoint_attrs in (
    ("Persistent", PersistentLLMTier, PersistentModelEndpoint, PersistentTierEndpoint, {"parent_model": PersistentTokenRange, "parent_kwarg": "range_id", "parent_field": "token_range", "next_order_fn": staticmethod(_next_order_for_range), "invalidate": staticmethod(invalidate_llm_bootstrap_cache)}, {"sibling_filter_fields": ("token_range", "intelligence_tier"), "invalidate": staticmethod(invalidate_llm_bootstrap_cache)}, {"allow_reasoning_override": True, "invalidate": staticmethod(invalidate_llm_bootstrap_cache)}),
    ("Browser", BrowserLLMTier, BrowserModelEndpoint, BrowserTierEndpoint, {"parent_model": BrowserLLMPolicy, "parent_field": "policy", "next_order_fn": staticmethod(_next_order_for_browser), "_get_parent": _active_browser_policy_parent}, {"sibling_filter_fields": ("policy", "intelligence_tier")}, {"allow_extraction_endpoint": True}),
):
    globals()[f"{_name}TierListCreateAPIView"] = type(f"{_name}TierListCreateAPIView", (_LLMTierListCreateAPIView,), {"__module__": __name__, "tier_model": _tier_model, **_list_attrs})
    globals()[f"{_name}TierDetailAPIView"] = type(f"{_name}TierDetailAPIView", (_LLMTierDetailAPIView,), {"__module__": __name__, "tier_model": _tier_model, **_detail_attrs})
    globals()[f"{_name}TierEndpointListCreateAPIView"] = type(f"{_name}TierEndpointListCreateAPIView", (_LLMTierEndpointListCreateAPIView,), {"__module__": __name__, "tier_model": _tier_model, "endpoint_model": _endpoint_model, "tier_endpoint_model": _tier_endpoint_model, **_endpoint_attrs})
    globals()[f"{_name}TierEndpointDetailAPIView"] = type(f"{_name}TierEndpointDetailAPIView", (_LLMTierEndpointDetailAPIView,), {"__module__": __name__, "endpoint_model": _endpoint_model, "tier_endpoint_model": _tier_endpoint_model, **_endpoint_attrs})
del _name, _tier_model, _endpoint_model, _tier_endpoint_model, _list_attrs, _detail_attrs, _endpoint_attrs


class AuxEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]
    endpoint_model = None
    include_supports_vision = False
    include_supports_image_to_image = False
    include_supports_image_to_video = False

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        endpoint, error_response = _create_aux_llm_endpoint_from_payload(
            payload,
            endpoint_model=self.endpoint_model,
            include_supports_vision=self.include_supports_vision,
            include_supports_image_to_image=self.include_supports_image_to_image,
            include_supports_image_to_video=self.include_supports_image_to_video,
        )
        if error_response:
            return error_response
        return _json_ok(endpoint_id=str(endpoint.id))


class AuxEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]
    endpoint_model = None
    include_supports_vision = False
    include_supports_image_to_image = False
    include_supports_image_to_video = False

    def patch(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(self.endpoint_model, pk=endpoint_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        error_response = _update_aux_llm_endpoint_from_payload(
            endpoint,
            payload,
            include_supports_vision=self.include_supports_vision,
            include_supports_image_to_image=self.include_supports_image_to_image,
            include_supports_image_to_video=self.include_supports_image_to_video,
        )
        if error_response:
            return error_response
        return _json_ok(endpoint_id=str(endpoint.id))

    def delete(self, request: HttpRequest, endpoint_id: str, *args: Any, **kwargs: Any):
        endpoint = get_object_or_404(self.endpoint_model, pk=endpoint_id)
        error_response = _delete_endpoint_with_tier_guard(endpoint)
        if error_response:
            return error_response
        return _json_ok()


class AuxTierListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]
    tier_model = None
    next_order_fn = None
    default_use_case = None
    valid_use_cases = None
    next_order_for_use_case_fn = None

    def _resolve_create_options(self, payload: dict[str, Any]):
        if self.default_use_case is None:
            return {}, self.next_order_fn, None

        use_case = (payload.get("use_case") or self.default_use_case).strip()
        valid_use_cases = set(self.valid_use_cases)
        if use_case not in valid_use_cases:
            allowed = ", ".join(sorted(valid_use_cases))
            return None, None, HttpResponseBadRequest(f"use_case must be one of: {allowed}")
        return {"use_case": use_case}, lambda: self.next_order_for_use_case_fn(use_case), None

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        extra_create_kwargs, next_order_fn, error_response = self._resolve_create_options(payload)
        if error_response:
            return error_response
        tier = _create_aux_tier_from_payload(
            payload,
            tier_model=self.tier_model,
            next_order_fn=next_order_fn,
            extra_create_kwargs=extra_create_kwargs,
        )
        return _json_ok(tier_id=str(tier.id))


class AuxTierDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]
    tier_model = None
    scope_queryset_by_use_case = False

    def _sibling_queryset(self, tier):
        if self.scope_queryset_by_use_case:
            return self.tier_model.objects.filter(use_case=tier.use_case)
        return self.tier_model.objects.all()

    def patch(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(self.tier_model, pk=tier_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        error_response = _update_aux_tier_from_payload(
            tier,
            payload,
            queryset=self._sibling_queryset(tier),
        )
        if error_response:
            return error_response
        return _json_ok(tier_id=str(tier.id))

    def delete(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(self.tier_model, pk=tier_id)
        tier.delete()
        return _json_ok()


class AuxTierEndpointListCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]
    tier_model = None
    endpoint_model = None
    tier_endpoint_model = None

    def post(self, request: HttpRequest, tier_id: str, *args: Any, **kwargs: Any):
        tier = get_object_or_404(self.tier_model, pk=tier_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        te, error_response = _create_aux_tier_endpoint_from_payload(
            payload,
            tier=tier,
            endpoint_model=self.endpoint_model,
            tier_endpoint_model=self.tier_endpoint_model,
        )
        if error_response:
            return error_response
        return _json_ok(tier_endpoint_id=str(te.id))


class AuxTierEndpointDetailAPIView(SystemAdminAPIView):
    http_method_names = ["patch", "delete"]
    tier_endpoint_model = None

    def patch(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(self.tier_endpoint_model, pk=tier_endpoint_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        error_response = _update_weighted_tier_endpoint_from_payload(tier_endpoint, payload)
        if error_response:
            return error_response
        return _json_ok(tier_endpoint_id=str(tier_endpoint.id))

    def delete(self, request: HttpRequest, tier_endpoint_id: str, *args: Any, **kwargs: Any):
        tier_endpoint = get_object_or_404(self.tier_endpoint_model, pk=tier_endpoint_id)
        tier_endpoint.delete()
        return _json_ok()


for _spec in (
    ("Embedding", EmbeddingsModelEndpoint, EmbeddingsLLMTier, EmbeddingsTierEndpoint, {"next_order_fn": staticmethod(_next_embedding_order)}, {}, {}),
    ("FileHandler", FileHandlerModelEndpoint, FileHandlerLLMTier, FileHandlerTierEndpoint, {"next_order_fn": staticmethod(_next_file_handler_order)}, {}, {"include_supports_vision": True}),
    ("ImageGeneration", ImageGenerationModelEndpoint, ImageGenerationLLMTier, ImageGenerationTierEndpoint, {"default_use_case": ImageGenerationLLMTier.UseCase.CREATE_IMAGE, "valid_use_cases": ImageGenerationLLMTier.UseCase.values, "next_order_for_use_case_fn": staticmethod(_next_image_generation_order)}, {"scope_queryset_by_use_case": True}, {"include_supports_image_to_image": True}),
    ("VideoGeneration", VideoGenerationModelEndpoint, VideoGenerationLLMTier, VideoGenerationTierEndpoint, {"default_use_case": VideoGenerationLLMTier.UseCase.CREATE_VIDEO, "valid_use_cases": VideoGenerationLLMTier.UseCase.values, "next_order_for_use_case_fn": staticmethod(_next_video_generation_order)}, {"scope_queryset_by_use_case": True}, {"include_supports_image_to_video": True}),
):
    _prefix, _endpoint_model, _tier_model, _tier_endpoint_model, _tier_list_attrs, _tier_detail_attrs, _endpoint_attrs = _spec
    globals()[f"{_prefix}EndpointListCreateAPIView"] = type(f"{_prefix}EndpointListCreateAPIView", (AuxEndpointListCreateAPIView,), {"__module__": __name__, "endpoint_model": _endpoint_model, **_endpoint_attrs})
    globals()[f"{_prefix}EndpointDetailAPIView"] = type(f"{_prefix}EndpointDetailAPIView", (AuxEndpointDetailAPIView,), {"__module__": __name__, "endpoint_model": _endpoint_model, **_endpoint_attrs})
    globals()[f"{_prefix}TierListCreateAPIView"] = type(f"{_prefix}TierListCreateAPIView", (AuxTierListCreateAPIView,), {"__module__": __name__, "tier_model": _tier_model, **_tier_list_attrs})
    globals()[f"{_prefix}TierDetailAPIView"] = type(f"{_prefix}TierDetailAPIView", (AuxTierDetailAPIView,), {"__module__": __name__, "tier_model": _tier_model, **_tier_detail_attrs})
    globals()[f"{_prefix}TierEndpointListCreateAPIView"] = type(f"{_prefix}TierEndpointListCreateAPIView", (AuxTierEndpointListCreateAPIView,), {"__module__": __name__, "tier_model": _tier_model, "endpoint_model": _endpoint_model, "tier_endpoint_model": _tier_endpoint_model})
    globals()[f"{_prefix}TierEndpointDetailAPIView"] = type(f"{_prefix}TierEndpointDetailAPIView", (AuxTierEndpointDetailAPIView,), {"__module__": __name__, "tier_endpoint_model": _tier_endpoint_model})
del _spec, _prefix, _endpoint_model, _tier_model, _tier_endpoint_model, _tier_list_attrs, _tier_detail_attrs, _endpoint_attrs


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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
                except (PersistentModelEndpoint.DoesNotExist, ValidationError):
                    return HttpResponseBadRequest("Invalid eval judge endpoint ID")

        if "summarization_endpoint_id" in payload:
            endpoint_id = payload.get("summarization_endpoint_id")
            if endpoint_id is None or endpoint_id == "":
                profile.summarization_endpoint = None
            else:
                try:
                    endpoint = PersistentModelEndpoint.objects.get(pk=endpoint_id)
                    profile.summarization_endpoint = endpoint
                except (PersistentModelEndpoint.DoesNotExist, ValidationError):
                    return HttpResponseBadRequest("Invalid summarization endpoint ID")

        if "agent_judge_endpoint_id" in payload:
            endpoint_id = payload.get("agent_judge_endpoint_id")
            if endpoint_id is None or endpoint_id == "":
                profile.agent_judge_endpoint = None
            else:
                try:
                    endpoint = PersistentModelEndpoint.objects.get(pk=endpoint_id)
                    profile.agent_judge_endpoint = endpoint
                except (PersistentModelEndpoint.DoesNotExist, ValidationError):
                    return HttpResponseBadRequest("Invalid agent judge endpoint ID")

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

        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
                summarization_endpoint=source.summarization_endpoint,
                agent_judge_endpoint=source.agent_judge_endpoint,
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

def _parse_weight(payload: dict[str, Any], default: float = 1.0) -> float | HttpResponseBadRequest:
    try:
        weight = float(payload.get("weight", default))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("weight must be numeric")
    if weight <= 0:
        return HttpResponseBadRequest("weight must be greater than zero")
    return weight


def _next_profile_tier_order(model, **filters) -> int:
    return (model.objects.filter(**filters).aggregate(max_order=Max("order")).get("max_order") or 0) + 1


for _name, _tier_model, _tier_endpoint_model, _endpoint_model, _list_attrs, _detail_attrs, _endpoint_attrs in (
    ("Persistent", ProfilePersistentTier, ProfilePersistentTierEndpoint, PersistentModelEndpoint, {"parent_model": ProfileTokenRange, "parent_kwarg": "range_id", "parent_field": "token_range", "order_by": ("intelligence_tier__rank", "order")}, {"sibling_filter_fields": ("token_range", "intelligence_tier"), "allow_order_update": True, "allow_intelligence_update": True}, {"allow_reasoning_override": True}),
    ("Browser", ProfileBrowserTier, ProfileBrowserTierEndpoint, BrowserModelEndpoint, {"parent_model": LLMRoutingProfile, "parent_kwarg": "profile_id", "parent_field": "profile", "order_by": ("intelligence_tier__rank", "order"), "auto_append_order": True}, {"sibling_filter_fields": ("profile", "intelligence_tier"), "allow_order_update": True, "allow_intelligence_update": True}, {"endpoint_model_attr": "browser_model", "allow_extraction_endpoint": True}),
    ("Embeddings", ProfileEmbeddingsTier, ProfileEmbeddingsTierEndpoint, EmbeddingsModelEndpoint, {"parent_model": LLMRoutingProfile, "parent_kwarg": "profile_id", "parent_field": "profile", "include_intelligence_tier": False}, {"allow_order_update": True}, {}),
):
    globals()[f"Profile{_name}TierListCreateAPIView"] = type(f"Profile{_name}TierListCreateAPIView", (_LLMTierListCreateAPIView,), {"__module__": __name__, "http_method_names": ["get", "post"], "tier_model": _tier_model, **_list_attrs})
    globals()[f"Profile{_name}TierDetailAPIView"] = type(f"Profile{_name}TierDetailAPIView", (_LLMTierDetailAPIView,), {"__module__": __name__, "tier_model": _tier_model, **_detail_attrs})
    globals()[f"Profile{_name}TierEndpointListCreateAPIView"] = type(f"Profile{_name}TierEndpointListCreateAPIView", (_LLMTierEndpointListCreateAPIView,), {"__module__": __name__, "http_method_names": ["get", "post"], "tier_model": _tier_model, "tier_endpoint_model": _tier_endpoint_model, "endpoint_model": _endpoint_model, "require_endpoint_id": True, "reject_duplicates": False, **_endpoint_attrs})
    globals()[f"Profile{_name}TierEndpointDetailAPIView"] = type(f"Profile{_name}TierEndpointDetailAPIView", (_LLMTierEndpointDetailAPIView,), {"__module__": __name__, "tier_endpoint_model": _tier_endpoint_model, "endpoint_model": _endpoint_model, **_endpoint_attrs})
del _name, _tier_model, _tier_endpoint_model, _endpoint_model, _list_attrs, _detail_attrs, _endpoint_attrs


@method_decorator(csrf_exempt, name="dispatch")
class AgentProcessingStatusAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        snapshot = build_processing_snapshot(agent)
        return JsonResponse(
            {
                "processing_active": snapshot.active,
                "processing_snapshot": serialize_processing_snapshot(snapshot),
                "signup_preview_state": agent.signup_preview_state,
                "planning_state": agent.planning_state,
                **serialize_agent_schedule(agent),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentStopAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        if not user_can_manage_agent(
            request.user,
            agent,
            allow_delinquent_personal_chat=True,
        ):
            return JsonResponse({"error": "Not permitted to stop this agent."}, status=403)

        set_processing_stop_requested(agent.id)
        clear_processing_work_state(agent.id)

        cancelled_web_task_count = 0
        if getattr(agent, "browser_use_agent_id", None):
            active_tasks = BrowserUseAgentTask.objects.alive().filter(
                agent_id=agent.browser_use_agent_id,
                status__in=[
                    BrowserUseAgentTask.StatusChoices.PENDING,
                    BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
                ],
            )
            for task in active_tasks:
                if cancel_browser_use_task(
                    task,
                    agent_id=str(agent.browser_use_agent_id),
                    source=AnalyticsSource.WEB,
                ):
                    cancelled_web_task_count += 1

        snapshot = build_processing_snapshot(agent)
        if not snapshot.active:
            clear_processing_stop_requested(agent.id)
        try:
            from console.agent_chat.signals import _broadcast_processing

            _broadcast_processing(agent)
        except Exception:
            logger.debug("Failed to broadcast processing update after stop for agent %s", agent.id, exc_info=True)

        return JsonResponse(
            {
                "stopping": True,
                "cancelledWebTaskCount": cancelled_web_task_count,
                "processing_active": snapshot.active,
                "processing_snapshot": serialize_processing_snapshot(snapshot),
                **serialize_agent_schedule(agent),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class AgentSuggestionsAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        try:
            prompt_count = int(request.GET.get("prompt_count", DEFAULT_PROMPT_COUNT))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("prompt_count must be an integer")

        processing = build_processing_snapshot(agent)
        if processing.active:
            return JsonResponse({"suggestions": [], "source": "none"})

        payload = build_agent_timeline_suggestions(agent, prompt_count=prompt_count)
        return JsonResponse(payload)


class AgentSettingsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        payload = build_agent_settings_payload(request, agent)
        return JsonResponse(payload)

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_manageable_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        return handle_agent_settings_mutation(request, agent)


class BillingInitialDataAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        return JsonResponse(build_billing_initial_data(request))


class AgentQuickSettingsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        payload = build_agent_quick_settings_payload(agent)
        return JsonResponse(payload)

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        owner = agent.organization or agent.user
        credit_settings = get_daily_credit_settings_for_owner(owner)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        daily_payload = payload.get("dailyCredits")
        previous_daily_limit = agent.daily_credit_limit
        daily_limit_changed = False
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
            daily_limit_changed = previous_daily_limit != new_daily_limit
            if daily_limit_changed:
                agent.daily_credit_limit = new_daily_limit
                agent.save(update_fields=["daily_credit_limit"])
        if daily_limit_changed:
            queue_settings_change_resume(
                agent,
                daily_credit_limit_changed=True,
                previous_daily_credit_limit=previous_daily_limit,
                source="agent_quick_settings_api",
            )

        payload = build_agent_quick_settings_payload(agent, owner)
        return JsonResponse(payload)


class AgentAddonsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    @staticmethod
    def _resolve_agent_addons_context(request: HttpRequest, agent_id: str):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_delinquent_personal_chat=True,
        )
        owner = agent.organization or agent.user
        plan_payload = (
            get_organization_plan(agent.organization)
            if agent.organization_id
            else reconcile_user_plan_from_stripe(agent.user)
        )
        can_manage_billing = _can_manage_contact_packs(request, agent, plan_payload)
        can_open_billing = _can_open_agent_billing(request, agent)
        return agent, owner, plan_payload, can_manage_billing, can_open_billing

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent, owner, _, can_manage_billing, can_open_billing = self._resolve_agent_addons_context(request, agent_id)
        payload = build_agent_addons_payload(
            agent,
            owner,
            can_manage_billing=can_manage_billing,
            can_open_billing=can_open_billing,
        )
        return JsonResponse(payload)

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent, owner, plan_payload, can_manage_billing, can_open_billing = self._resolve_agent_addons_context(
            request,
            agent_id,
        )
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
        task_packs_submitted = False

        for label, pack_payload, update_func in packs_to_process:
            if pack_payload is None:
                continue
            if label == "taskPacks":
                task_packs_submitted = True
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

        if task_packs_submitted:
            resumed_count = queue_owner_task_pack_resume(
                owner_id=getattr(owner, "id", None),
                owner_type=owner_type,
                source="agent_addons_api_owner_resume",
            )
            if resumed_count == 0:
                queue_settings_change_resume(
                    agent,
                    task_pack_changed=True,
                    source="agent_addons_api",
                )

        payload = build_agent_addons_payload(
            agent,
            owner,
            can_manage_billing=can_manage_billing,
            can_open_billing=can_open_billing,
        )
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
        servers = [
            _serialize_mcp_server(server, request=request, pending_servers=pending_servers)
            for server in queryset
        ]
        return JsonResponse(
            {
                "owner_scope": owner_scope,
                "owner_label": owner_label,
                "result_count": len(servers),
                "servers": servers,
            }
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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


class MCPServerTestAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_mcp_server_config(request, server_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        return _run_mcp_server_test(server, payload)


class PlatformMCPServerListAPIView(SystemAdminAPIView):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        queryset = list(
            MCPServerConfig.objects.select_related("oauth_credential")
            .filter(scope=MCPServerConfig.Scope.PLATFORM)
            .order_by("display_name")
        )
        pending_servers: set[str] = set()
        if queryset:
            server_ids = [server.id for server in queryset]
            pending_servers = {
                str(server_id)
                for server_id in MCPServerOAuthSession.objects.filter(
                    server_config_id__in=server_ids,
                    initiated_by=request.user,
                    expires_at__gt=timezone.now(),
                ).values_list("server_config_id", flat=True)
            }
        servers = [
            _serialize_mcp_server(server, request=request, pending_servers=pending_servers)
            for server in queryset
        ]
        return JsonResponse(
            {
                "owner_scope": MCPServerConfig.Scope.PLATFORM,
                "owner_label": "Platform",
                "result_count": len(servers),
                "servers": servers,
            }
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        form = MCPServerConfigForm(payload, allow_commands=True, allow_prefetch_apps=True)
        if form.is_valid():
            try:
                with transaction.atomic():
                    server = form.save(platform=True)
            except IntegrityError:
                form.add_error("name", "A server with that identifier already exists.")
            else:
                get_mcp_manager().refresh_server(str(server.id))
                _track_org_event_for_console(
                    request,
                    AnalyticsEvent.MCP_SERVER_CREATED,
                    _mcp_server_event_properties(request, server, MCPServerConfig.Scope.PLATFORM),
                )
                return JsonResponse(
                    {
                        "server": _serialize_mcp_server_detail(server, request),
                        "message": "MCP server saved.",
                    },
                    status=201,
                )

        return JsonResponse({"errors": _form_errors(form)}, status=400)


class PlatformMCPServerDetailAPIView(SystemAdminAPIView):
    http_method_names = ["get", "patch", "delete"]

    def get(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_platform_mcp_server_config(request, server_id)
        return JsonResponse({"server": _serialize_mcp_server_detail(server, request)})

    def patch(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_platform_mcp_server_config(request, server_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

        form = MCPServerConfigForm(payload, instance=server, allow_commands=True, allow_prefetch_apps=True)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(platform=True)
            except IntegrityError:
                form.add_error("name", "A server with that identifier already exists.")
            else:
                get_mcp_manager().refresh_server(str(updated.id))
                _track_org_event_for_console(
                    request,
                    AnalyticsEvent.MCP_SERVER_UPDATED,
                    _mcp_server_event_properties(request, updated, MCPServerConfig.Scope.PLATFORM),
                )
                return JsonResponse(
                    {
                        "server": _serialize_mcp_server_detail(updated, request),
                        "message": "MCP server updated.",
                    }
                )

        return JsonResponse({"errors": _form_errors(form)}, status=400)

    def delete(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_platform_mcp_server_config(request, server_id)
        server_name = server.display_name
        props = _mcp_server_event_properties(request, server, MCPServerConfig.Scope.PLATFORM)
        cached_server_id = str(server.id)
        server.delete()
        get_mcp_manager().remove_server(cached_server_id)
        _track_org_event_for_console(
            request,
            AnalyticsEvent.MCP_SERVER_DELETED,
            props,
        )
        return JsonResponse({"message": f"MCP server '{server_name}' was deleted."})


class PlatformMCPServerTestAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, server_id: str, *args: Any, **kwargs: Any):
        server = _resolve_platform_mcp_server_config(request, server_id)
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload
        return _run_mcp_server_test(server, payload)


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

        config = _resolve_mcp_server_config(request, str(config_id), allow_platform_staff=True)
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

        config = _resolve_mcp_server_config(request, str(config_id), allow_platform_staff=True)
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
        config = _resolve_mcp_server_config(request, str(server_config_id), allow_platform_staff=True)
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
        config = _resolve_mcp_server_config(request, str(server_config_id), allow_platform_staff=True)
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
            reverse("app-email-oauth-callback-view")
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
            reverse("app-email-oauth-callback-view")
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

        account_update_fields: list[str] = []
        oauth_mode_fields = (
            ("connection_mode", AgentEmailAccount.ConnectionMode.OAUTH2),
            ("smtp_auth", AgentEmailAccount.AuthMode.OAUTH2),
            ("imap_auth", AgentEmailAccount.ImapAuthMode.OAUTH2),
        )
        for field, value in oauth_mode_fields:
            if getattr(account, field) != value:
                setattr(account, field, value)
                account_update_fields.append(field)

        for field in ("smtp_username", "imap_username"):
            if not getattr(account, field):
                setattr(account, field, account.endpoint.address)
                account_update_fields.append(field)
        if account_update_fields:
            account.save(update_fields=[*account_update_fields, "updated_at"])

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
        if isinstance(payload := _json_payload_or_bad_request(request), HttpResponseBadRequest):
            return payload

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


def _parse_session_visibility(payload: dict | None) -> bool:
    if not payload or "is_visible" not in payload:
        return True
    raw = payload.get("is_visible")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    raise ValueError("is_visible must be a boolean")


def _session_response(result) -> JsonResponse:
    session = result.session
    return JsonResponse({
        "session_key": str(session.session_key),
        "ttl_seconds": result.ttl_seconds,
    })


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionStartAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            ttl = _parse_ttl(body)
            is_visible = _parse_session_visibility(body)
            result = start_web_session(
                agent,
                request.user,
                ttl_seconds=ttl,
                is_visible=is_visible,
            )
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
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        try:
            ttl = _parse_ttl(body)
            session_key = _parse_session_key(body)
            is_visible = _parse_session_visibility(body)
            result = heartbeat_web_session(
                session_key,
                agent,
                request.user,
                ttl_seconds=ttl,
                is_visible=is_visible,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class AgentWebSessionEndAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = resolve_agent_for_request(
            request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )
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
