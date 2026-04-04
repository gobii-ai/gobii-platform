import json
from types import SimpleNamespace
from typing import Any

from allauth.account.adapter import get_adapter
from allauth.account.internal.flows.manage_email import email_already_exists
from allauth.account.models import EmailAddress
from allauth.account.signals import user_signed_up
from allauth.account.utils import setup_user_email
from django.conf import settings
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.http import Http404, HttpRequest, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import exceptions

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.human_input_requests import list_pending_human_input_requests
from api.agent.comms.message_service import ingest_inbound_message
from api.models import (
    AgentFileSpaceAccess,
    AgentFsNode,
    CommsChannel,
    NativeAppSession,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    UserPreference,
    build_web_agent_address,
    build_web_user_address,
)
from api.pipedream_app_utils import normalize_app_slugs
from api.services.system_settings import (
    get_account_allow_password_login,
    get_account_allow_password_signup,
)
from api.services.web_sessions import (
    WEB_SESSION_TTL_SECONDS,
    end_web_session,
    heartbeat_web_session,
    start_web_session,
    touch_web_session,
)
from app_api.auth import (
    NativeAppAuthentication,
    authenticate_native_app_refresh_token,
    create_native_app_session,
    revoke_native_app_session,
    rotate_native_app_session,
)
from console.agent_chat.access import (
    agent_queryset_for,
    resolve_agent,
    shared_agent_queryset_for,
    user_can_manage_agent,
    user_is_collaborator,
)
from console.agent_chat.timeline import (
    HIDE_IN_CHAT_PAYLOAD_KEY,
    fetch_timeline_window,
    serialize_message_event,
    serialize_processing_snapshot,
)
from console.agent_creation import create_persistent_agent_from_charter
from console.context_helpers import _ALLOWED_MANAGE_ROLES, resolve_console_context
from console.context_overrides import CONTEXT_ID_HEADER, CONTEXT_TYPE_HEADER, get_context_override
from middleware.console_timezone import ConsoleApiTimezoneInferenceMiddleware
from console.phone_utils import get_primary_phone, serialize_phone
from console.api_views import (
    _build_filespace_download_response,
    _current_user_email_is_verified,
    _ensure_console_endpoints,
    _parse_session_key,
    _parse_session_visibility,
    _parse_ttl,
    _path_meta,
    _session_response,
    _web_chat_properties,
)
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.onboarding import (
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    set_trial_onboarding_intent,
    set_trial_onboarding_requires_plan_selection,
)
from util.trial_enforcement import PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE, TrialRequiredValidationError


APP_TIMELINE_DOWNLOAD_ROUTE = "app_api:agent_fs_download"


def _json_error(message: str, status: int, error_code: str | None = None, **extra: Any) -> JsonResponse:
    payload: dict[str, Any] = {"error": message}
    if error_code:
        payload["errorCode"] = error_code
    payload.update(extra)
    return JsonResponse(payload, status=status)


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object.")
    return payload


def _extract_password_confirmation(payload: dict[str, Any]) -> str:
    return str(
        payload.get("passwordConfirmation")
        or payload.get("password_confirmation")
        or payload.get("password2")
        or ""
    )


def _serialize_validation_error(exc: ValidationError) -> str:
    messages = []
    if hasattr(exc, "message_dict"):
        for field_errors in exc.message_dict.values():
            messages.extend(field_errors)
    messages.extend(getattr(exc, "messages", []))
    if not messages:
        messages.append("The request was invalid.")
    return str(messages[0])


def _resolve_app_context_info(request: HttpRequest):
    user = request.user
    override = get_context_override(request) or {"type": "personal", "id": str(user.id)}
    return resolve_console_context(user, None, override=override)


def _apply_app_context_override(request: HttpRequest):
    context_info = _resolve_app_context_info(request)
    request.META[f"HTTP_{CONTEXT_TYPE_HEADER.upper().replace('-', '_')}"] = context_info.current_context.type
    request.META[f"HTTP_{CONTEXT_ID_HEADER.upper().replace('-', '_')}"] = context_info.current_context.id
    return context_info


def _resolve_agent_for_app_request(
    request: HttpRequest,
    agent_id: str,
    *,
    allow_shared: bool = False,
    allow_delinquent_personal_chat: bool = False,
) -> PersistentAgent:
    context_info = _resolve_app_context_info(request)
    return resolve_agent(
        request.user,
        None,
        agent_id,
        context_override={
            "type": context_info.current_context.type,
            "id": context_info.current_context.id,
        },
        allow_shared=allow_shared,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    )


def _serialize_user(user) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email or "",
        "username": user.username or "",
        "firstName": user.first_name or "",
        "lastName": user.last_name or "",
        "fullName": user.get_full_name() or "",
    }


def _serialize_current_context(context_info) -> dict[str, Any]:
    current_context = context_info.current_context
    payload = {
        "type": current_context.type,
        "id": current_context.id,
        "name": current_context.name,
    }
    if context_info.current_membership is not None:
        payload["role"] = context_info.current_membership.role
        payload["canManageAgents"] = context_info.can_manage_org_agents
    else:
        payload["canManageAgents"] = True
    return payload


def _serialize_available_contexts(user, current_context: dict[str, Any]) -> list[dict[str, Any]]:
    personal_label = user.get_full_name() or user.username or user.email or "Personal"
    payload = [
        {
            "type": "personal",
            "id": str(user.id),
            "name": personal_label,
            "canManageAgents": True,
            "isCurrent": current_context["type"] == "personal" and current_context["id"] == str(user.id),
        }
    ]
    memberships = (
        OrganizationMembership.objects.select_related("org")
        .filter(
            user=user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        .order_by("org__name")
    )
    for membership in memberships:
        payload.append(
            {
                "type": "organization",
                "id": str(membership.org.id),
                "name": membership.org.name,
                "role": membership.role,
                "canManageAgents": membership.role in _ALLOWED_MANAGE_ROLES,
                "isCurrent": (
                    current_context["type"] == "organization"
                    and current_context["id"] == str(membership.org.id)
                ),
            }
        )
    return payload


def _build_me_payload(request: HttpRequest, *, include_context_override: bool = True) -> dict[str, Any]:
    if include_context_override:
        context_info = _resolve_app_context_info(request)
    else:
        context_info = resolve_console_context(
            request.user,
            None,
            override={"type": "personal", "id": str(request.user.id)},
        )
    current_context = _serialize_current_context(context_info)
    return {
        "user": _serialize_user(request.user),
        "emailVerification": {
            "email": request.user.email or "",
            "isVerified": _current_user_email_is_verified(request.user),
            "verificationRequired": settings.ACCOUNT_EMAIL_VERIFICATION != "none",
        },
        "phone": serialize_phone(get_primary_phone(request.user)),
        "timezone": UserPreference.resolve_user_timezone(request.user, fallback_to_utc=False),
        "currentContext": current_context,
        "availableContexts": _serialize_available_contexts(request.user, current_context),
    }


def _build_auth_payload(request: HttpRequest, credentials) -> dict[str, Any]:
    payload = _build_me_payload(request)
    payload["tokens"] = {
        "accessToken": credentials.access_token,
        "refreshToken": credentials.refresh_token,
        "accessExpiresAt": credentials.access_expires_at.isoformat(),
        "refreshExpiresAt": credentials.refresh_expires_at.isoformat(),
    }
    return payload


def _parse_app_session_visibility(payload: dict[str, Any] | None) -> bool:
    normalized_payload = dict(payload or {})
    if "is_visible" not in normalized_payload and "isVisible" in normalized_payload:
        normalized_payload["is_visible"] = normalized_payload["isVisible"]
    return _parse_session_visibility(normalized_payload)


class AppTokenRequiredMixin:
    authentication_class = NativeAppAuthentication

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        authenticator = self.authentication_class()
        try:
            auth_result = authenticator.authenticate(request)
        except exceptions.AuthenticationFailed as exc:
            return _json_error(str(exc), 401, "AUTHENTICATION_FAILED")
        if auth_result is None:
            return _json_error("Authentication required", 401, "AUTHENTICATION_REQUIRED")
        user, native_session = auth_result
        request.user = user
        request._cached_user = user
        request.auth = native_session
        # Bearer auth happens after middleware, so app requests need an
        # explicit timezone inference pass once the user is attached.
        ConsoleApiTimezoneInferenceMiddleware._maybe_store_inferred_timezone(request)
        return super().dispatch(request, *args, **kwargs)


def _emit_app_login_event(user, *, signed_up: bool = False) -> None:
    properties: dict[str, Any] = {}
    if signed_up:
        properties["signed_up"] = True
    Analytics.track_event(
        user_id=str(user.id),
        event=AnalyticsEvent.LOGGED_IN,
        source=AnalyticsSource.APP,
        properties=properties,
    )


def _annotate_last_message_fields(queryset):
    hidden_key = f"raw_payload__{HIDE_IN_CHAT_PAYLOAD_KEY}"
    visible_messages = (
        PersistentAgentMessage.objects.filter(owner_agent=models.OuterRef("pk"))
        .filter(models.Q(**{hidden_key: False}) | models.Q(**{f"{hidden_key}__isnull": True}))
        .order_by("-timestamp", "-id")
    )
    return queryset.annotate(
        last_message_body=models.Subquery(visible_messages.values("body")[:1]),
        last_message_timestamp=models.Subquery(visible_messages.values("timestamp")[:1]),
        last_message_is_outbound=models.Subquery(visible_messages.values("is_outbound")[:1]),
    )


def _create_agent_response(request: HttpRequest, payload: dict[str, Any]) -> JsonResponse:
    initial_message = str(payload.get("message") or "").strip()
    if not initial_message:
        return _json_error("Message is required", 400, "MESSAGE_REQUIRED")
    preferred_llm_tier_key = str(payload.get("preferred_llm_tier") or "").strip() or None
    charter_override = str(payload.get("charter_override") or "").strip() or None
    try:
        selected_pipedream_app_slugs = normalize_app_slugs(
            payload.get("selected_pipedream_app_slugs"),
            strict=True,
            require_list=True,
        )
    except ValueError as exc:
        return _json_error(str(exc), 400, "INVALID_APP_SELECTION")

    try:
        _apply_app_context_override(request)
        result = create_persistent_agent_from_charter(
            request,
            initial_message=initial_message,
            contact_email=(request.user.email or "").strip(),
            email_enabled=bool((request.user.email or "").strip()),
            sms_enabled=False,
            preferred_contact_method="web",
            web_enabled=True,
            preferred_llm_tier_key=preferred_llm_tier_key,
            charter_override=charter_override,
            selected_pipedream_app_slugs=selected_pipedream_app_slugs,
        )
    except PermissionDenied:
        return _json_error("Invalid context override.", 403, "INVALID_CONTEXT")
    except TrialRequiredValidationError:
        set_trial_onboarding_intent(request, target=TRIAL_ONBOARDING_TARGET_AGENT_UI)
        set_trial_onboarding_requires_plan_selection(request, required=True)
        return _json_error(
            PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE,
            400,
            "TRIAL_REQUIRED",
            onboardingTarget=TRIAL_ONBOARDING_TARGET_AGENT_UI,
            requiresPlanSelection=True,
        )
    except ValidationError as exc:
        message = _serialize_validation_error(exc)
        error_code = "AGENT_CREATE_INVALID"
        if "seat" in message.lower():
            error_code = "NO_ORG_SEATS"
        return _json_error(message, 400, error_code)
    except IntegrityError:
        return _json_error(
            "We ran into a problem creating your agent. Please try again.",
            500,
            "AGENT_CREATE_FAILED",
        )

    agent_email_endpoint = (
        result.agent.comms_endpoints.filter(channel=CommsChannel.EMAIL)
        .order_by("-is_primary")
        .first()
    )
    return JsonResponse(
        {
            "agentId": str(result.agent.id),
            "agentName": result.agent.name,
            "agentEmail": agent_email_endpoint.address if agent_email_endpoint else None,
        },
        status=201,
    )


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppSignUpAPIView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if not get_account_allow_password_signup():
            return _json_error("Password signup is currently disabled.", 403, "PASSWORD_SIGNUP_DISABLED")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        email = str(payload.get("email") or "").strip().lower()
        password = str(payload.get("password") or payload.get("password1") or "")
        password_confirmation = _extract_password_confirmation(payload)
        if not email:
            return _json_error("Email is required.", 400, "EMAIL_REQUIRED")
        if not password:
            return _json_error("Password is required.", 400, "PASSWORD_REQUIRED")
        if password_confirmation and password != password_confirmation:
            return _json_error("Passwords do not match.", 400, "PASSWORD_MISMATCH")

        adapter = get_adapter(request)
        try:
            if not adapter.is_open_for_signup(request):
                return _json_error("Signups are currently disabled.", 403, "SIGNUP_DISABLED")
            email = adapter.clean_email(email)
            email, _ = email_already_exists(email, always_raise=True)
            adapter.clean_password(password)
        except ValidationError as exc:
            return _json_error(_serialize_validation_error(exc), 400, "SIGNUP_VALIDATION_ERROR")

        try:
            with transaction.atomic():
                user = adapter.new_user(request)
                user = adapter.save_user(
                    request,
                    user,
                    SimpleNamespace(cleaned_data={"email": email, "password1": password}),
                    commit=True,
                )
                setup_user_email(request, user, [])
                user_signed_up.send(sender=user.__class__, request=request, user=user)
        except IntegrityError:
            return _json_error("A user is already registered with this email address.", 400, "EMAIL_TAKEN")

        primary_email = EmailAddress.objects.filter(user=user, primary=True).first()
        if primary_email and settings.ACCOUNT_EMAIL_VERIFICATION != "none":
            primary_email.send_confirmation(request, signup=True)

        request.user = user
        request._cached_user = user
        credentials = create_native_app_session(user, request=request, body=payload)
        _emit_app_login_event(user, signed_up=True)
        return JsonResponse(_build_auth_payload(request, credentials), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppSignInAPIView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if not get_account_allow_password_login():
            return _json_error("Password login is currently disabled.", 403, "PASSWORD_LOGIN_DISABLED")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        email = str(payload.get("email") or "").strip().lower()
        password = str(payload.get("password") or "")
        if not email or not password:
            return _json_error("Email and password are required.", 400, "INVALID_LOGIN")

        user = authenticate(request, email=email, password=password)
        if user is None:
            user = authenticate(request, username=email, password=password)
        if user is None:
            return _json_error("The email address and/or password you specified are not correct.", 401, "INVALID_LOGIN")

        request.user = user
        request._cached_user = user
        credentials = create_native_app_session(user, request=request, body=payload)
        _emit_app_login_event(user)
        return JsonResponse(_build_auth_payload(request, credentials))


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppRefreshAPIView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        refresh_token = str(payload.get("refreshToken") or payload.get("refresh_token") or "").strip()
        if not refresh_token:
            return _json_error("refreshToken is required.", 400, "REFRESH_TOKEN_REQUIRED")
        session = authenticate_native_app_refresh_token(refresh_token)
        if session is None:
            return _json_error("Invalid or expired refresh token.", 401, "INVALID_REFRESH_TOKEN")

        request.user = session.user
        request._cached_user = session.user
        credentials = rotate_native_app_session(session, request=request, body=payload)
        return JsonResponse(
            {
                "tokens": {
                    "accessToken": credentials.access_token,
                    "refreshToken": credentials.refresh_token,
                    "accessExpiresAt": credentials.access_expires_at.isoformat(),
                    "refreshExpiresAt": credentials.refresh_expires_at.isoformat(),
                }
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppLogoutAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        session = request.auth
        if not isinstance(session, NativeAppSession):
            return _json_error("Authentication required", 401, "AUTHENTICATION_REQUIRED")
        revoke_native_app_session(session)
        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.LOGGED_OUT,
            source=AnalyticsSource.APP,
            properties={},
        )
        return JsonResponse({"revoked": True})


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppEmailResendVerificationAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        email_address = EmailAddress.objects.filter(user=request.user, primary=True).first()
        if not email_address:
            return _json_error("No email address found.", 400, "EMAIL_NOT_FOUND")
        if email_address.verified:
            return JsonResponse({"verified": True, "message": "Email already verified."})
        email_address.send_confirmation(request, signup=False)
        return JsonResponse({"verified": False, "message": "Verification email sent."})


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppMeAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _build_me_payload(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "INVALID_CONTEXT")
        return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppAgentRosterAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            context_info = _resolve_app_context_info(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "INVALID_CONTEXT")

        agents_qs = _annotate_last_message_fields(
            agent_queryset_for(
                request.user,
                context_info.current_context,
                allow_delinquent_personal_chat=True,
            )
            .select_related("agent_color")
            .order_by("name")
        )
        shared_qs = _annotate_last_message_fields(
            shared_agent_queryset_for(request.user).select_related("agent_color")
        )
        agent_ids = list(agents_qs.values_list("id", flat=True))
        if agent_ids:
            shared_qs = shared_qs.exclude(id__in=agent_ids)
        agents = list(agents_qs) + list(shared_qs.order_by("name"))
        collaborators_by_agent_id = {agent.id for agent in shared_qs}
        from console.agent_chat.timeline import build_processing_activity_map

        processing_activity_by_agent_id = build_processing_activity_map(agents)
        resolved_preferences = UserPreference.resolve_known_preferences(request.user)
        payload = []
        for agent in agents:
            preview_text = (agent.last_message_body or "").strip() or None
            payload.append(
                {
                    "id": str(agent.id),
                    "name": agent.name or "",
                    "avatarUrl": agent.get_avatar_url(),
                    "displayColorHex": agent.get_display_color(),
                    "miniDescription": agent.mini_description or "",
                    "shortDescription": agent.short_description or "",
                    "isOrgOwned": agent.organization_id is not None,
                    "isCollaborator": agent.id in collaborators_by_agent_id,
                    "canManageAgent": user_can_manage_agent(
                        request.user,
                        agent,
                        allow_delinquent_personal_chat=True,
                    ),
                    "lastInteractionAt": (
                        agent.last_interaction_at.isoformat() if agent.last_interaction_at else None
                    ),
                    "lastMessagePreview": preview_text,
                    "lastMessageAt": (
                        agent.last_message_timestamp.isoformat()
                        if agent.last_message_timestamp
                        else None
                    ),
                    "lastMessageIsOutbound": (
                        bool(agent.last_message_is_outbound)
                        if agent.last_message_is_outbound is not None
                        else None
                    ),
                    "processingActive": processing_activity_by_agent_id.get(str(agent.id), False),
                    "signupPreviewState": agent.signup_preview_state,
                }
            )
        return JsonResponse(
            {
                "currentContext": _serialize_current_context(context_info),
                "sortMode": resolved_preferences.get(UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE),
                "favoriteAgentIds": resolved_preferences.get(
                    UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS,
                    [],
                ),
                "agents": payload,
            }
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return _create_agent_response(request, payload)

@method_decorator(csrf_exempt, name="dispatch")
class NativeAppAgentTimelineAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        direction = (request.GET.get("direction") or "initial").lower()
        if direction not in {"initial", "older", "newer"}:
            return HttpResponseBadRequest("Invalid direction parameter")
        try:
            agent = _resolve_agent_for_app_request(
                request,
                agent_id,
                allow_shared=True,
                allow_delinquent_personal_chat=True,
            )
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "FORBIDDEN")
        try:
            limit = int(request.GET.get("limit", 50))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")
        window = fetch_timeline_window(
            agent,
            cursor=request.GET.get("cursor") or None,
            direction=direction,
            limit=limit,
            download_route_name=APP_TIMELINE_DOWNLOAD_ROUTE,
        )
        return JsonResponse(
            {
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
                "signup_preview_state": agent.signup_preview_state,
                "pending_human_input_requests": list_pending_human_input_requests(agent),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppAgentMessageCreateAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_agent_for_app_request(
                request,
                agent_id,
                allow_shared=True,
                allow_delinquent_personal_chat=True,
            )
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "FORBIDDEN")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        message_text = str(payload.get("body") or "").strip()
        if not message_text:
            return _json_error("Message body is required.", 400, "MESSAGE_REQUIRED")

        sender_address, recipient_address = _ensure_console_endpoints(agent, request.user)
        session_result = touch_web_session(
            agent,
            request.user,
            source="app_message",
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
            attachments=[],
            raw_payload={"source": "native_app", "user_id": request.user.id},
            msg_channel=CommsChannel.WEB,
        )
        info = ingest_inbound_message(CommsChannel.WEB, parsed, filespace_import_mode="sync")
        event = serialize_message_event(
            info.message,
            download_route_name=APP_TIMELINE_DOWNLOAD_ROUTE,
        )
        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.APP_CHAT_MESSAGE_SENT,
            source=AnalyticsSource.APP,
            properties=_web_chat_properties(
                agent,
                {
                    "message_id": str(info.message.id),
                    "message_length": len(message_text),
                    "session_key": (
                        str(session_result.session.session_key)
                        if session_result is not None
                        else None
                    ),
                },
            ),
        )
        return JsonResponse({"event": event}, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppHumanInputRequestResponseAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, request_id: str, *args: Any, **kwargs: Any):
        from api.agent.comms.human_input_requests import submit_human_input_response

        try:
            agent = _resolve_agent_for_app_request(request, agent_id, allow_shared=True)
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "FORBIDDEN")
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
            return _json_error("This request is no longer pending.", 400, "REQUEST_NOT_PENDING")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        selected_option_key = str(payload.get("selected_option_key") or "").strip() or None
        free_text = str(payload.get("free_text") or "").strip() or None
        if bool(selected_option_key) == bool(free_text):
            return _json_error(
                "Provide exactly one of selected_option_key or free_text.",
                400,
                "INVALID_RESPONSE",
            )
        try:
            message = submit_human_input_response(
                human_input_request,
                selected_option_key=selected_option_key,
                free_text=free_text,
                actor_user_id=request.user.id,
            )
        except ValueError as exc:
            return _json_error(str(exc), 400, "INVALID_RESPONSE")
        return JsonResponse(
            {
                "event": serialize_message_event(
                    message,
                    download_route_name=APP_TIMELINE_DOWNLOAD_ROUTE,
                ),
                "pending_human_input_requests": list_pending_human_input_requests(agent),
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppHumanInputRequestBatchResponseAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        from api.agent.comms.human_input_requests import submit_human_input_responses_batch

        try:
            agent = _resolve_agent_for_app_request(request, agent_id, allow_shared=True)
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "FORBIDDEN")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        responses = payload.get("responses")
        if not isinstance(responses, list) or not responses:
            return _json_error("Provide a non-empty responses array.", 400, "RESPONSES_REQUIRED")
        normalized_responses = []
        for response in responses:
            if not isinstance(response, dict):
                return _json_error("Each batch response must be an object.", 400, "INVALID_RESPONSE")
            request_item_id = str(response.get("request_id") or "").strip()
            selected_option_key = str(response.get("selected_option_key") or "").strip()
            free_text = str(response.get("free_text") or "").strip()
            if not request_item_id:
                return _json_error("Each batch response must include request_id.", 400, "INVALID_RESPONSE")
            if bool(selected_option_key) == bool(free_text):
                return _json_error(
                    "Each batch response must include exactly one of selected_option_key or free_text.",
                    400,
                    "INVALID_RESPONSE",
                )
            normalized_responses.append(
                {
                    "request_id": request_item_id,
                    "selected_option_key": selected_option_key,
                    "free_text": free_text,
                }
            )
        try:
            message = submit_human_input_responses_batch(
                agent,
                normalized_responses,
                actor_user_id=request.user.id,
            )
        except ValueError as exc:
            return _json_error(str(exc), 400, "INVALID_RESPONSE")
        return JsonResponse(
            {
                "event": serialize_message_event(
                    message,
                    download_route_name=APP_TIMELINE_DOWNLOAD_ROUTE,
                ),
                "pending_human_input_requests": list_pending_human_input_requests(agent),
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppAgentFsNodeDownloadAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = get_object_or_404(
            PersistentAgent.objects.alive().select_related("organization"),
            pk=agent_id,
        )
        if not user_can_manage_agent(
            request.user,
            agent,
            allow_delinquent_personal_chat=True,
        ) and not user_is_collaborator(request.user, agent):
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
        except (TypeError, ValidationError, ValueError):
            return HttpResponseBadRequest("Invalid node_id")
        if not node:
            raise Http404("File not found.")

        parent_path, _ = _path_meta(node.path)
        Analytics.track_event(
            user_id=str(request.user.id),
            event=AnalyticsEvent.AGENT_FILE_DOWNLOADED,
            source=AnalyticsSource.APP,
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
        return _build_filespace_download_response(node)


def _track_app_session_event(user_id: str, event, agent: PersistentAgent, properties: dict[str, Any]) -> None:
    Analytics.track_event(
        user_id=user_id,
        event=event,
        source=AnalyticsSource.APP,
        properties=_web_chat_properties(agent, properties),
    )


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppAgentSessionStartAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_agent_for_app_request(
                request,
                agent_id,
                allow_shared=True,
                allow_delinquent_personal_chat=True,
            )
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "FORBIDDEN")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        try:
            ttl = _parse_ttl(payload)
            is_visible = _parse_app_session_visibility(payload)
            result = start_web_session(
                agent,
                request.user,
                source="app_start",
                ttl_seconds=ttl,
                is_visible=is_visible,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        _track_app_session_event(
            str(request.user.id),
            AnalyticsEvent.APP_CHAT_SESSION_STARTED,
            agent,
            {
                "session_key": str(result.session.session_key),
                "session_ttl_seconds": result.ttl_seconds,
            },
        )
        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppAgentSessionHeartbeatAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_agent_for_app_request(
                request,
                agent_id,
                allow_shared=True,
                allow_delinquent_personal_chat=True,
            )
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "FORBIDDEN")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        try:
            ttl = _parse_ttl(payload)
            session_key = _parse_session_key(payload)
            is_visible = _parse_app_session_visibility(payload)
            result = heartbeat_web_session(
                session_key,
                agent,
                request.user,
                source="app_heartbeat",
                ttl_seconds=ttl,
                is_visible=is_visible,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return _session_response(result)


@method_decorator(csrf_exempt, name="dispatch")
class NativeAppAgentSessionEndAPIView(AppTokenRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        try:
            agent = _resolve_agent_for_app_request(
                request,
                agent_id,
                allow_shared=True,
                allow_delinquent_personal_chat=True,
            )
        except PermissionDenied as exc:
            return _json_error(str(exc), 403, "FORBIDDEN")
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        try:
            session_key = _parse_session_key(payload)
            result = end_web_session(session_key, agent, request.user)
        except ValueError as exc:
            if str(exc) == "Unknown web session.":
                return JsonResponse({"session_key": session_key, "ended": True})
            return HttpResponseBadRequest(str(exc))

        session = result.session
        properties = {
            "session_key": str(session.session_key),
            "session_ttl_seconds": result.ttl_seconds,
        }
        if session.ended_at:
            properties["session_ended_at"] = session.ended_at.isoformat()
        _track_app_session_event(
            str(request.user.id),
            AnalyticsEvent.APP_CHAT_SESSION_ENDED,
            agent,
            properties,
        )
        return _session_response(result)
