import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View

from api.services.native_integrations import (
    GOOGLE_DRIVE_PROVIDER,
    NativeIntegrationAuthError,
    NativeIntegrationConfigurationError,
    NativeIntegrationFileListError,
    NativeIntegrationTokenRequestError,
    build_native_integration_permission_summary,
    build_oauth_credentials_bundle,
    delete_native_integration_credentials,
    disable_overlapping_pipedream_tools_for_native_integration,
    get_native_integration_provider,
    get_native_integration_secret,
    list_native_integration_credential_fields,
    list_native_integration_providers,
    load_native_integration_credentials,
    list_google_drive_accessible_files,
    native_integration_client_credentials,
    new_oauth_state,
    refresh_oauth_credentials_if_needed,
    request_oauth_token,
    save_native_integration_credentials,
    trigger_agents_for_native_integration_change,
    upsert_manual_native_integration_credentials,
)
from api.services.native_integration_events import normalize_native_integration_event_files, record_native_integration_agent_event, resolve_native_integration_event_agent
from console.context_helpers import build_console_context

NATIVE_INTEGRATION_STATE_SALT = "gobii.native_integrations.oauth_state"
NATIVE_INTEGRATION_STATE_MAX_AGE_SECONDS = 10 * 60


def _permission_denied_response(exc: PermissionDenied) -> JsonResponse:
    messages = getattr(exc, "args", None)
    message = str(messages[0]) if messages else "Permission denied."
    return JsonResponse({"error": message}, status=403)


def _validation_error_response(exc: ValidationError, *, status: int = 400) -> JsonResponse:
    errors = exc.message_dict if hasattr(exc, "message_dict") else {"non_field_errors": exc.messages}
    return JsonResponse({"errors": errors}, status=status)


def _resolve_native_integration_owner(request: HttpRequest):
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization integrations.")
        return "organization", None, membership.org
    return "user", request.user, None


def _owner_id(owner_user, owner_org) -> str:
    return str(owner_org.id if owner_org is not None else owner_user.id)


def _serialize_provider(provider, owner_user, owner_org) -> dict[str, Any]:
    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    credentials: dict[str, Any] = {}
    credentials_valid = False
    if secret is not None:
        try:
            credentials = load_native_integration_credentials(secret)
            credentials_valid = True
        except NativeIntegrationAuthError:
            credentials = {}
    permission_summary = build_native_integration_permission_summary(
        provider,
        credentials if credentials_valid else None,
        connected=secret is not None and credentials_valid,
    )

    return {
        "provider_key": provider.key,
        "display_name": provider.display_name,
        "description": provider.description,
        "auth_type": provider.auth_type,
        "icon": provider.icon,
        "api_hosts": list(provider.api_hosts),
        "scopes": list(provider.scopes),
        "connected": bool(permission_summary["connected"]),
        "scope": credentials.get("scope") or "",
        "granted_scopes": permission_summary["granted_scopes"],
        "requested_scopes": permission_summary["requested_scopes"],
        "available_capabilities": permission_summary["available_capabilities"],
        "missing_capabilities": permission_summary["missing_capabilities"],
        "missing_scopes": permission_summary["missing_scopes"],
        "credential_fields": [
            field.to_dict()
            for field in list_native_integration_credential_fields(provider.key)
        ],
        "present_credential_fields": permission_summary["present_credential_fields"],
        "missing_credential_fields": permission_summary["missing_credential_fields"],
        "capability_summary": permission_summary["status_text"],
        "setup_url": permission_summary["setup_url"],
        "expires_at": credentials.get("expires_at"),
        "connect_url": reverse("console-native-integration-connect", args=[provider.key]),
        "files_url": reverse("console-native-integration-files", args=[provider.key]),
        "picker_token_url": reverse("console-native-integration-picker-token", args=[provider.key]),
        "agent_event_url": reverse("console-native-integration-agent-events", args=[provider.key]),
        "revoke_url": reverse("console-native-integration-revoke", args=[provider.key]),
    }


def _sign_oauth_state(provider_key: str, request: HttpRequest, owner_scope: str, owner_user, owner_org) -> str:
    payload = {
        "provider_key": provider_key,
        "user_id": str(request.user.id),
        "owner_scope": owner_scope,
        "owner_id": _owner_id(owner_user, owner_org),
        "nonce": new_oauth_state(),
    }
    return signing.dumps(payload, salt=NATIVE_INTEGRATION_STATE_SALT, compress=True)


def _load_oauth_state(state: str) -> dict[str, Any]:
    try:
        payload = signing.loads(
            state,
            salt=NATIVE_INTEGRATION_STATE_SALT,
            max_age=NATIVE_INTEGRATION_STATE_MAX_AGE_SECONDS,
        )
    except signing.BadSignature as exc:
        raise ValidationError({"state": "OAuth session expired. Restart the flow."}) from exc
    if not isinstance(payload, dict):
        raise ValidationError({"state": "OAuth session is invalid. Restart the flow."})
    return payload


def _validate_oauth_state(
    request: HttpRequest,
    provider_key: str,
    state: str,
    owner_scope: str,
    owner_user,
    owner_org,
) -> dict[str, Any]:
    payload = _load_oauth_state(state)
    if payload.get("provider_key") != provider_key:
        raise ValidationError({"state": "OAuth provider mismatch."})
    if payload.get("user_id") != str(request.user.id):
        raise PermissionDenied("OAuth session belongs to another user.")
    if payload.get("owner_scope") != owner_scope or payload.get("owner_id") != _owner_id(owner_user, owner_org):
        raise PermissionDenied("OAuth session belongs to another workspace context.")
    return payload


class NativeIntegrationListAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        providers = [
            _serialize_provider(provider, owner_user, owner_org)
            for provider in list_native_integration_providers()
        ]
        owner_label = owner_org.name if owner_org is not None else (request.user.get_full_name() or request.user.username)
        return JsonResponse(
            {
                "owner_scope": owner_scope,
                "owner_label": owner_label,
                "providers": providers,
            }
        )


class NativeIntegrationConnectAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        if provider.auth_type == "manual":
            try:
                body = request.body.decode("utf-8")
            except UnicodeDecodeError:
                return HttpResponseBadRequest("Invalid request body")

            try:
                payload = json.loads(body or "{}")
            except json.JSONDecodeError:
                return HttpResponseBadRequest("Invalid JSON body")
            if not isinstance(payload, dict):
                return HttpResponseBadRequest("Invalid JSON payload: expected an object")

            credentials = payload.get("credentials") or {}
            try:
                _, owner_user, owner_org = _resolve_native_integration_owner(request)
                secret = upsert_manual_native_integration_credentials(
                    provider,
                    owner_user,
                    owner_org,
                    credentials,
                )
                stored = load_native_integration_credentials(secret)
                permission_summary = build_native_integration_permission_summary(provider, stored)
            except ValidationError as exc:
                return _validation_error_response(exc)
            except PermissionDenied as exc:
                return _permission_denied_response(exc)
            except NativeIntegrationAuthError as exc:
                return JsonResponse({"error": str(exc)}, status=400)

            with transaction.atomic():
                disable_overlapping_pipedream_tools_for_native_integration(provider.key, owner_user, owner_org)
                if permission_summary["connected"]:
                    trigger_agents_for_native_integration_change(provider.key, owner_user, owner_org)

            return JsonResponse(
                {
                    "connected": bool(permission_summary["connected"]),
                    "provider_key": provider.key,
                    "secret_id": str(secret.id),
                    "present_credential_fields": permission_summary["present_credential_fields"],
                    "missing_credential_fields": permission_summary["missing_credential_fields"],
                },
                status=201,
            )

        if provider.auth_type != "oauth2":
            return HttpResponseBadRequest("This provider does not use OAuth 2.0.")

        client_id, client_secret = native_integration_client_credentials(provider)
        if not client_id or not client_secret:
            return JsonResponse({"error": f"{provider.display_name} OAuth is not configured."}, status=400)

        try:
            owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        state = _sign_oauth_state(provider.key, request, owner_scope, owner_user, owner_org)
        redirect_uri = request.build_absolute_uri(reverse("console-native-integration-oauth-callback-view"))

        query = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": provider.scope_string,
            "state": state,
        }
        query.update(provider.authorization_params)
        authorization_url = f"{provider.authorization_endpoint}?{urlencode(query)}"

        return JsonResponse(
            {
                "provider_key": provider.key,
                "authorization_url": authorization_url,
                "state": state,
                "expires_at": (timezone.now() + timedelta(seconds=NATIVE_INTEGRATION_STATE_MAX_AGE_SECONDS)).isoformat(),
            },
            status=201,
        )


class NativeIntegrationCallbackAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            body = request.body.decode("utf-8")
        except UnicodeDecodeError:
            return HttpResponseBadRequest("Invalid request body")

        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")
        if not isinstance(payload, dict):
            return HttpResponseBadRequest("Invalid JSON payload: expected an object")

        code = str(payload.get("authorization_code") or "").strip()
        state = str(payload.get("state") or "").strip()
        if not code or not state:
            return HttpResponseBadRequest("authorization_code and state are required")

        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        try:
            owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
            _validate_oauth_state(request, provider.key, state, owner_scope, owner_user, owner_org)
        except ValidationError as exc:
            return _validation_error_response(exc)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        client_id, client_secret = native_integration_client_credentials(provider)
        if not client_id or not client_secret:
            return JsonResponse({"error": f"{provider.display_name} OAuth is not configured."}, status=400)

        redirect_uri = request.build_absolute_uri(reverse("console-native-integration-oauth-callback-view"))
        try:
            token_payload = request_oauth_token(
                provider,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                request_error_message="Token exchange failed",
                endpoint_error_message="Token endpoint returned an error",
                invalid_json_message="Token endpoint returned non-JSON payload",
            )
        except NativeIntegrationTokenRequestError as exc:
            error_payload = {"error": str(exc)}
            if exc.detail:
                error_payload["detail"] = exc.detail
            if exc.response_body:
                error_payload["status_code"] = exc.status_code
                error_payload["body"] = exc.response_body
            return JsonResponse(error_payload, status=exc.status_code)

        existing_credentials = {}
        existing_secret = get_native_integration_secret(provider.key, owner_user, owner_org)
        if existing_secret is not None:
            try:
                existing_credentials = load_native_integration_credentials(existing_secret)
            except NativeIntegrationAuthError:
                existing_credentials = {}

        try:
            credentials = build_oauth_credentials_bundle(
                provider,
                token_payload,
                existing_credentials=existing_credentials,
            )
        except ValidationError as exc:
            return _validation_error_response(exc, status=502)

        with transaction.atomic():
            secret = save_native_integration_credentials(provider, owner_user, owner_org, credentials)
            disable_overlapping_pipedream_tools_for_native_integration(provider.key, owner_user, owner_org)

        return JsonResponse(
            {
                "connected": True,
                "provider_key": provider.key,
                "secret_id": str(secret.id),
                "scope": credentials.get("scope") or "",
                "expires_at": credentials.get("expires_at"),
            }
        )


class NativeIntegrationRevokeAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        deleted = delete_native_integration_credentials(provider.key, owner_user, owner_org)
        return JsonResponse({"revoked": deleted})


class NativeIntegrationPickerTokenAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        if provider.key != GOOGLE_DRIVE_PROVIDER.key:
            return JsonResponse({"error": f"{provider.display_name} does not support file picking."}, status=400)

        if not settings.GOOGLE_PICKER_API_KEY or not settings.GOOGLE_PICKER_APP_ID:
            return JsonResponse({"error": "Google Picker is not configured."}, status=400)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        secret = get_native_integration_secret(provider.key, owner_user, owner_org)
        if secret is None:
            return JsonResponse({"error": f"{provider.display_name} is not connected."}, status=404)

        try:
            credentials = load_native_integration_credentials(secret)
            credentials = refresh_oauth_credentials_if_needed(provider, secret, credentials)
        except NativeIntegrationConfigurationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except NativeIntegrationAuthError as exc:
            return JsonResponse({"error": str(exc)}, status=401)

        access_token = str(credentials.get("access_token") or "")
        if not access_token:
            return JsonResponse({"error": f"{provider.display_name} must be reconnected."}, status=401)

        response = JsonResponse(
            {
                "access_token": access_token,
                "developer_key": settings.GOOGLE_PICKER_API_KEY,
                "app_id": settings.GOOGLE_PICKER_APP_ID,
                "scope": credentials.get("scope") or provider.scope_string,
                "expires_at": credentials.get("expires_at"),
            }
        )
        response["Cache-Control"] = "no-store"
        return response


class NativeIntegrationFilesAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        if provider.key != GOOGLE_DRIVE_PROVIDER.key:
            return JsonResponse({"error": f"{provider.display_name} does not expose files."}, status=400)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)
        secret = get_native_integration_secret(provider.key, owner_user, owner_org)
        if secret is None:
            return JsonResponse({"error": f"{provider.display_name} is not connected."}, status=404)

        try:
            files = list_google_drive_accessible_files(secret)
        except NativeIntegrationConfigurationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except NativeIntegrationFileListError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status_code)
        except NativeIntegrationAuthError as exc:
            return JsonResponse({"error": str(exc)}, status=401)

        response = JsonResponse({"provider_key": provider.key, "files": [file.to_dict() for file in files]})
        response["Cache-Control"] = "no-store"
        return response


class NativeIntegrationAgentEventAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            body = request.body.decode("utf-8")
        except UnicodeDecodeError:
            return HttpResponseBadRequest("Invalid request body")

        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")
        if not isinstance(payload, dict):
            return HttpResponseBadRequest("Invalid JSON payload: expected an object")

        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        try:
            _, owner_user, owner_org = _resolve_native_integration_owner(request)
            agent = resolve_native_integration_event_agent(
                str(payload.get("agent_id") or "").strip(),
                owner_user=owner_user,
                owner_org=owner_org,
            )
            files = normalize_native_integration_event_files(payload.get("files"))
            step = record_native_integration_agent_event(
                agent=agent,
                provider=provider,
                event_type=str(payload.get("event_type") or "").strip(),
                files=files,
                source="console.native_integrations_api.NativeIntegrationAgentEventAPIView",
            )
        except ValidationError as exc:
            return _validation_error_response(exc)
        except PermissionDenied as exc:
            return _permission_denied_response(exc)

        return JsonResponse(
            {
                "recorded": True,
                "provider_key": provider.key,
                "agent_id": str(agent.id),
                "step_id": str(step.id),
            },
            status=201,
        )
