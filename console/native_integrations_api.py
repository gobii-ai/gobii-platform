import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
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
    build_oauth_credentials_bundle,
    delete_native_integration_credentials,
    get_native_integration_provider,
    get_native_integration_secret,
    list_native_integration_providers,
    load_native_integration_credentials,
    native_integration_client_credentials,
    new_oauth_state,
    refresh_oauth_credentials_if_needed,
    save_native_integration_credentials,
)
from api.services.native_integration_files import (
    native_integration_granted_file_queryset,
    serialize_native_integration_granted_file,
    upsert_native_integration_granted_files,
)
from console.context_helpers import build_console_context

NATIVE_INTEGRATION_STATE_SALT = "gobii.native_integrations.oauth_state"
NATIVE_INTEGRATION_STATE_MAX_AGE_SECONDS = 10 * 60


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
    if secret is not None:
        try:
            credentials = load_native_integration_credentials(secret)
        except NativeIntegrationAuthError:
            credentials = {}

    return {
        "provider_key": provider.key,
        "display_name": provider.display_name,
        "description": provider.description,
        "auth_type": provider.auth_type,
        "icon": provider.icon,
        "api_hosts": list(provider.api_hosts),
        "scopes": list(provider.scopes),
        "connected": secret is not None,
        "scope": credentials.get("scope") or "",
        "expires_at": credentials.get("expires_at"),
        "connect_url": reverse("console-native-integration-connect", args=[provider.key]),
        "files_url": reverse("console-native-integration-files", args=[provider.key]),
        "picker_token_url": reverse("console-native-integration-picker-token", args=[provider.key]),
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
        owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
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

        if provider.auth_type != "oauth2":
            return HttpResponseBadRequest("This provider does not use OAuth 2.0.")

        client_id, client_secret = native_integration_client_credentials(provider)
        if not client_id or not client_secret:
            return JsonResponse({"error": f"{provider.display_name} OAuth is not configured."}, status=400)

        owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
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

        code = str(payload.get("authorization_code") or "").strip()
        state = str(payload.get("state") or "").strip()
        if not code or not state:
            return HttpResponseBadRequest("authorization_code and state are required")

        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        owner_scope, owner_user, owner_org = _resolve_native_integration_owner(request)
        try:
            _validate_oauth_state(request, provider.key, state, owner_scope, owner_user, owner_org)
        except ValidationError as exc:
            return JsonResponse({"errors": exc.message_dict}, status=400)

        client_id, client_secret = native_integration_client_credentials(provider)
        if not client_id or not client_secret:
            return JsonResponse({"error": f"{provider.display_name} OAuth is not configured."}, status=400)

        redirect_uri = request.build_absolute_uri(reverse("console-native-integration-oauth-callback-view"))
        try:
            response = httpx.post(
                provider.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15.0,
            )
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
            return JsonResponse({"error": "Token endpoint returned non-JSON payload"}, status=502)

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
            return JsonResponse({"errors": exc.message_dict}, status=502)

        with transaction.atomic():
            secret = save_native_integration_credentials(provider, owner_user, owner_org, credentials)

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

        _, owner_user, owner_org = _resolve_native_integration_owner(request)
        deleted = delete_native_integration_credentials(provider.key, owner_user, owner_org)
        native_integration_granted_file_queryset(owner_user, owner_org, provider.key).delete()
        return JsonResponse({"revoked": deleted})


class NativeIntegrationFilesAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        _, owner_user, owner_org = _resolve_native_integration_owner(request)
        files = [
            serialize_native_integration_granted_file(granted_file)
            for granted_file in native_integration_granted_file_queryset(owner_user, owner_org, provider.key).order_by(
                "name",
                "external_file_id",
            )
        ]
        return JsonResponse({"provider_key": provider.key, "files": files})

    def post(self, request: HttpRequest, provider_key: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        if provider.key != GOOGLE_DRIVE_PROVIDER.key:
            return JsonResponse({"error": f"{provider.display_name} does not support selected files."}, status=400)

        try:
            body = request.body.decode("utf-8")
        except UnicodeDecodeError:
            return HttpResponseBadRequest("Invalid request body")

        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON body")

        files_payload = payload.get("files")
        if not isinstance(files_payload, list):
            return JsonResponse({"errors": {"files": "This field must be a list."}}, status=400)

        _, owner_user, owner_org = _resolve_native_integration_owner(request)
        if get_native_integration_secret(provider.key, owner_user, owner_org) is None:
            return JsonResponse({"error": f"{provider.display_name} is not connected."}, status=404)

        try:
            saved_files = upsert_native_integration_granted_files(
                provider.key,
                owner_user,
                owner_org,
                files_payload,
                selected_by=request.user,
            )
        except ValidationError as exc:
            error_payload = exc.message_dict if hasattr(exc, "message_dict") else {"files": exc.messages}
            return JsonResponse({"errors": error_payload}, status=400)

        return JsonResponse(
            {
                "provider_key": provider.key,
                "upserted_count": len(saved_files),
                "files": [serialize_native_integration_granted_file(granted_file) for granted_file in saved_files],
            },
            status=201,
        )


class NativeIntegrationFileDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["delete"]

    def delete(self, request: HttpRequest, provider_key: str, file_id: str, *args: Any, **kwargs: Any):
        try:
            provider = get_native_integration_provider(provider_key)
        except KeyError:
            return JsonResponse({"error": "Unknown native integration provider."}, status=404)

        _, owner_user, owner_org = _resolve_native_integration_owner(request)
        deleted_count, _ = native_integration_granted_file_queryset(owner_user, owner_org, provider.key).filter(
            id=file_id,
        ).delete()
        if deleted_count == 0:
            return JsonResponse({"error": "Selected file not found."}, status=404)
        return JsonResponse({"deleted": True})


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

        _, owner_user, owner_org = _resolve_native_integration_owner(request)
        secret = get_native_integration_secret(provider.key, owner_user, owner_org)
        if secret is None:
            return JsonResponse({"error": f"{provider.display_name} is not connected."}, status=404)

        try:
            credentials = load_native_integration_credentials(secret)
            credentials = refresh_oauth_credentials_if_needed(provider, secret, credentials)
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
